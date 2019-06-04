from django.db.models import Q
from django.utils import timezone

from feeds.models import Source, Enclosure, Post

import feedparser

import time
import datetime

from bs4 import BeautifulSoup
from urllib.parse import urljoin
import requests
import io
import pyrfc3339
import json

from django.conf import settings

import hashlib

import logging


def fix_relative(html, url):

    """ this is fucking cheesy """
    try:
        base = "/".join(url.split("/")[:3])

        html = html.replace("src='/", "src='%s/" % base)
        html = html.replace('src="/', 'src="%s/' % base)
    
    except Exception as ex:
        pass    

    return html
        

def update_feeds(max_feeds=3, output=None):

    if  output is None:
        output = io.StringIO()

    todo = Source.objects.filter(Q(due_poll__lt = timezone.now()) & Q(live = True))

    
    output.write("Queue size is {}".format(todo.count()))

    sources = todo.order_by("due_poll")[:max_feeds]

    output.write("\nProcessing %d\n\n" % sources.count())


    for src in sources:
        read_feed(src, output)
    
    
def read_feed(source_feed, output=None):

    old_interval = source_feed.interval

    if  output is None:
        output = io.StringIO()

    was302 = False
    
    output.write("\n------------------------------\n")
    
    source_feed.last_polled = timezone.now()

    headers = { "User-Agent": "{user_agent} (+{server}; Updater; {subs} subscribers)".format(user_agent=settings.FEEDS_USER_AGENT, server=settings.FEEDS_SERVER, subs=source_feed.num_subs),  } #identify ourselves 

    if source_feed.etag:
        headers["If-None-Match"] = str(source_feed.etag)
    if source_feed.last_modified:
        headers["If-Modified-Since"] = str(source_feed.last_modified)

    output.write("\nFetching %s" % source_feed.feed_url)
    
    ret = None
    try:
        ret = requests.get(source_feed.feed_url, headers=headers, allow_redirects=False, timeout=20)
        source_feed.status_code = ret.status_code
        source_feed.last_result = "Unhandled Case"
        output.write(str(ret))
    except Exception as ex:
        logging.error("Fetch feed  error: " + str(ex))
        source_feed.last_result = ("Fetch error:" + str(ex))[:255]
        source_feed.status_code = 0
        output.write("\nFetch error: " + str(ex))


        
    if ret is None and source_feed.status_code == 1:   # er ??
        pass
    elif ret == None or source_feed.status_code == 0:
        source_feed.interval += 120
    elif ret.status_code < 200 or ret.status_code >= 500:
        #errors, impossible return codes
        source_feed.interval += 120
        source_feed.last_result = "Server error fetching feed (%d)" % ret.status_code
    elif ret.status_code == 404:
        #not found
        source_feed.interval += 120
        source_feed.last_result = "The feed could not be found"
    elif ret.status_code == 403 or ret.status_code == 410: #Forbidden or gone

        if "Cloudflare" in ret.text or ("Server" in ret.headers and "cloudflare" in ret.headers["Server"]):
            source_feed.is_cloudflare = True
            source_feed.last_result = "Blocked by Cloudflare (grr)"
        else:
            source_feed.last_result = "Feed is no longer accessible."
        source_feed.live = False
            
    elif ret.status_code >= 400 and ret.status_code < 500:
        #treat as bad request
        source_feed.live = False
        source_feed.last_result = "Bad request (%d)" % ret.status_code
    elif ret.status_code == 304:
        #not modified
        source_feed.interval += 10
        source_feed.last_result = "Not modified"
        source_feed.last_success = timezone.now()
        
        if source_feed.last_success and (timezone.now() - source_feed.last_success).days > 7:
            source_feed.last_result = "Clearing etag/last modified due to lack of changes"
            source_feed.etag = None
            source_feed.last_modified = None
        
        
    
    elif ret.status_code == 301 or ret.status_code == 308: #permenant redirect
        new_url = ""
        try:
            if "Location" in ret.headers:
                new_url = ret.headers["Location"]
            
                if new_url[0] == "/":
                    #find the domain from the feed
                    
                    base = "/".join(source_feed.feed_url.split("/")[:3])
                    
                
                    new_url = base + new_url


                source_feed.feed_url = new_url
            
                source_feed.last_result = "Moved"
            else:
                source_feed.last_result = "Feed has moved but no location provided"
        except exception as Ex:
            output.write("\nError redirecting.")
            source_feed.last_result = "Error redirecting feed to " + new_url  
            pass
    elif ret.status_code == 302 or ret.status_code == 303 or ret.status_code == 307: #Temporary redirect
        new_url = ""
        was302 = True
        try:
            new_url = ret.headers["Location"]
            
            if new_url[0] == "/":
                #find the domain from the feed
                start = source_feed.feed_url[:8]
                end = source_feed.feed_url[8:]
                if end.find("/") >= 0:
                    end = end[:end.find("/")]
                
                new_url = start + end + new_url
                
            
            ret = requests.get(new_url, headers=headers, allow_redirects=True, timeout=20)
            source_feed.status_code = ret.status_code
            source_feed.last_result = "Temporary Redirect to " + new_url

            if source_feed.last_302_url == new_url:
                #this is where we 302'd to last time
                td = timezone.now() - source_feed.last_302_start
                if td.days > 60:
                    source_feed.feed_url = new_url
                    source_feed.last_302_url = " "
                    source_feed.last_302_start = None
                    source_feed.last_result = "Permanent Redirect to " + new_url 
                else:
                    source_feed.last_result = "Temporary Redirect to " + new_url + " since " + source_feed.last_302_start.strftime("%d %B")

            else:
                source_feed.last_302_url = new_url
                source_feed.last_302_start = timezone.now()

                source_feed.last_result = "Temporary Redirect to " + new_url + " since " + source_feed.last_302_start.strftime("%d %B")


        except Exception as ex:     
            source_feed.last_result = "Failed Redirection to " + new_url +  " " + str(ex)
            source_feed.interval += 60
    
    #NOT ELIF, WE HAVE TO START THE IF AGAIN TO COPE WTIH 302
    if ret and ret.status_code >= 200 and ret.status_code < 300: #now we are not following redirects 302,303 and so forth are going to fail here, but what the hell :)

        # great!
        ok = True
        changed = False 
        
        
        if was302:
            source_feed.etag = None
            source_feed.last_modified = None
        else:
            try:
                source_feed.etag = ret.headers["etag"]
            except Exception as ex:
                source_feed.etag = None                                   
            try:
                source_feed.last_modified = ret.headers["Last-Modified"]
            except Exception as ex:
                source_feed.last_modified = None                                   
        
        output.write("\netag:%s\nLast Mod:%s\n\n" % (source_feed.etag,source_feed.last_modified))


        content_type = "Not Set"
        if "Content-Type" in ret.headers:
            content_type = ret.headers["Content-Type"]

        (ok,changed) = import_feed(source_feed=source_feed, feed_body=ret.text.strip(), content_type=content_type, output=output)
        
        if ok and changed:
            source_feed.interval /= 2
            source_feed.last_result = " OK (updated)" #and temporary redirects
            source_feed.last_change = timezone.now()
            
        elif ok:
            source_feed.last_result = "OK"
            source_feed.interval += 20 # we slow down feeds a little more that don't send headers we can use
        else: #not OK
            source_feed.interval += 120
            
    if source_feed.interval < 60:
        source_feed.interval = 60 # no less than 1 hour
    if source_feed.interval > (60 * 24):
        source_feed.interval = (60 * 24) # no more than a day
    
    output.write("\nUpdating source_feed.interval from %d to %d\n" % (old_interval, source_feed.interval))
    td = datetime.timedelta(minutes=source_feed.interval)
    source_feed.due_poll = timezone.now() + td
    source_feed.save()
        

def import_feed(source_feed, feed_body, content_type, output=None):

    ok = False
    changed = False
        
    if "xml" in content_type or feed_body[0:1] == "<":
        (ok,changed) = parse_feed_xml(source_feed, feed_body, output)
    elif "json" in content_type or feed_body[0:1] == "{":
        (ok,changed) = parse_feed_json(source_feed, feed_body, output)
    else:
        ok = False
        source_feed.last_result = "Unknown Feed Type: " + content_type

    if ok and changed:
        source_feed.last_result = " OK (updated)" #and temporary redirects
        source_feed.last_change = timezone.now()
        
        idx = source_feed.max_index
        # give indices to posts based on created date
        posts = Post.objects.filter(Q(source=source_feed) & Q(index=0)).order_by("created")
        for p in posts:
            idx += 1
            p.index = idx
            p.save()
            
        source_feed.max_index = idx
    
    return (ok, changed)
    

    
def parse_feed_xml(source_feed, feed_content, output):

    ok = True
    changed = False 

    #output.write(ret.content)           
    try:
        f = feedparser.parse(feed_content) #need to start checking feed parser errors here
        entries = f['entries']
        if len(entries):
            source_feed.last_success = timezone.now() #in case we start auto unsubscribing long dead feeds
        else:
            source_feed.last_result = "Feed is empty"
            ok = False

    except Exception as ex:
        source_feed.last_result = "Feed Parse Error"
        entries = []
        ok = False
    
    if ok:

        try:
            source_feed.site_url = f.feed.link
            source_feed.name = f.feed.title
        except Exception as ex:
            pass
    

        #output.write(entries)
        entries.reverse() # Entries are typically in reverse chronological order - put them in right order
        for e in entries:
            try:
                if e.content[0].type == "text/plain":
                    raise
                body = e.content[0].value
            except Exception as ex:
                try:
                    body = e.summary                    
                except Exception as ex:
                    body = " "

            body = fix_relative(body, source_feed.site_url)



            try:
                guid = e.guid
            except Exception as ex:
                try:
                    guid = e.link
                except Exception as ex:
                    m = hashlib.md5()
                    m.update(body.encode("utf-8"))
                    guid = m.hexdigest()
                    
            try:
                p  = Post.objects.filter(source=source_feed).filter(guid=guid)[0]
                output.write("EXISTING " + guid + "\n")

            except Exception as ex:
                output.write("NEW " + guid + "\n")
                p = Post(index=0)
                p.found = timezone.now()
                changed = True
                p.source = source_feed
    
            try:
                title = e.title
            except Exception as ex:
                title = ""
                        
            try:
                p.link = e.link
            except Exception as ex:
                p.link = ''
            p.title = title

            try:
        
                p.created  = datetime.datetime.fromtimestamp(time.mktime(e.published_parsed)).replace(tzinfo=timezone.utc)

            except Exception as ex:
                output.write("CREATED ERROR")     
                p.created  = timezone.now()
        
            p.guid = guid
            try:
                p.author = e.author
            except Exception as ex:
                p.author = ""

            try:
                seen_files = []
                for ee in list(p.enclosure_set.all()):
                    # check existing enclosure is still there
                    found_enclosure = False
                    for pe in e["enclosures"]:
                    
                        if pe["href"] == ee.href and ee.href not in seen_files:
                            found_enclosure = True
                        
                            try:
                                ee.length = int(pe["length"])
                            except:
                                ee.length = 0

                            try:
                                type = pe["type"]
                            except:
                                type = "audio/mpeg"  # we are assuming podcasts here but that's probably not safe

                            ee.type = type
                            ee.save()
                            break
                    if not found_enclosure:
                        ee.delete()
                    seen_files.append(ee.href)
    
                for pe in e["enclosures"]:
                    try:
                        if pe["href"] not in seen_files:
                    
                            try:
                                length = int(pe["length"])
                            except:
                                length = 0
                            
                            try:
                                type = pe["type"]
                            except:
                                type = "audio/mpeg"
                    
                            ee = Enclosure(post=p , href=pe["href"], length=length, type=type)
                            ee.save()
                    except Exception as ex:
                        pass
            except Exception as ex:
                if output:
                    output.write("No enclosures - " + str(ex))



            try:
                p.body = body                          
                p.save()
                # output.write(p.body)
            except Exception as ex:
                #output.write(str(sys.exc_info()[0]))
                output.write("\nSave error for post:" + str(sys.exc_info()[0]))
                traceback.print_tb(sys.exc_info()[2],file=output)

    return (ok,changed)
    
    
    
def parse_feed_json(source_feed, feed_content, output):

    ok = True
    changed = False 

    try:
        f = json.loads(feed_content)
        entries = f['items']
        if len(entries):
            source_feed.last_success = timezone.now() #in case we start auto unsubscribing long dead feeds
        else:
            source_feed.last_result = "Feed is empty"
            source_feed.interval += 120
            ok = False

    except Exception as ex:
        source_feed.last_result = "Feed Parse Error"
        entries = []
        source_feed.interval += 120
        ok = False
    
    if ok:
    
        if "expired" in f and f["expired"]:
            # This feed says it is done
            # TODO: permanently disable
            # for now source_feed.interval to max
            source_feed.interval = (24*3*60)
            source_feed.last_result = "This feed has expired"
            return (False,False,source_feed.interval)

        try:
            source_feed.site_url = f["home_page_url"]
            source_feed.name = f["title"]
        except Exception as ex:
            pass
    

        #output.write(entries)
        entries.reverse() # Entries are typically in reverse chronological order - put them in right order
        for e in entries:
            body = " "
            if "content_text" in e:
                body = e["content_text"]
            if "content_html" in e:
                body = e["content_html"] # prefer html over text
                
            body = fix_relative(body,source_feed.site_url)

            try:
                guid = e["id"]
            except Exception as ex:
                try:
                    guid = e["url"]
                except Exception as ex:
                    m = hashlib.md5()
                    m.update(body.encode("utf-8"))
                    guid = m.hexdigest()
                    
            try:
                p  = Post.objects.filter(source=source_feed).filter(guid=guid)[0]
                output.write("EXISTING " + guid + "\n")

            except Exception as ex:
                output.write("NEW " + guid + "\n")
                p = Post(index=0)
                p.found = timezone.now()
                changed = True
                p.source = source_feed
    
            try:
                title = e["title"]
            except Exception as ex:
                title = ""      
                
            # borrow the RSS parser's sanitizer
            body  = feedparser._sanitizeHTML(body, "utf-8", 'text/html') # TODO: validate charset ??
            title = feedparser._sanitizeHTML(title, "utf-8", 'text/html') # TODO: validate charset ??
            # no other fields are ever marked as |safe in the templates

                        
            try:
                p.link = e["url"]
            except Exception as ex:
                p.link = ''
            
            p.title = title

            try:
                p.created  = pyrfc3339.parse(e["date_published"])
            except Exception as ex:
                output.write("CREATED ERROR")     
                p.created  = timezone.now()
        
        
            p.guid = guid
            try:
                p.author = e["author"]
            except Exception as ex:
                p.author = ""

            try:
                p.body = body                       
                p.save()
                # output.write(p.body)
            except Exception as ex:
                #output.write(str(sys.exc_info()[0]))
                output.write("\nSave error for post:" + str(sys.exc_info()[0]))
                traceback.print_tb(sys.exc_info()[2],file=output)

    return (ok,changed)