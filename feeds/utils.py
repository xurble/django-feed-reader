from django.db.models import Q
from django.utils import timezone
from django.conf import settings

from feeds.models import Source, Enclosure, Post, WebProxy

import feedparser as parser

import time
import datetime

from bs4 import BeautifulSoup
from urllib.parse import urljoin
import requests

import pyrfc3339
import json



import hashlib
from random import choice
import logging



class NullOutput(object):
    # little class for when we have no outputter    
    def write(self, str):
        pass


def _customize_sanitizer(fp):

    bad_attributes = [
        "align",
        "valign",
        "hspace",
        "width",
        "height"
    ]
    
    for item in bad_attributes:
        try:
            if item in fp.sanitizer._HTMLSanitizer.acceptable_attributes:
                fp.sanitizer._HTMLSanitizer.acceptable_attributes.remove(item)
        except Exception:
            logging.debug("Could not remove {}".format(item))
            

def get_agent(source_feed):

    if source_feed.is_cloudflare:
        agent = random_user_agent()
        logging.error("using agent: {}".format(agent))
    else:
        agent = "{user_agent} (+{server}; Updater; {subs} subscribers)".format(user_agent=settings.FEEDS_USER_AGENT, server=settings.FEEDS_SERVER, subs=source_feed.num_subs)

    return agent

def random_user_agent():

    return choice([
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/12.1.1 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/51.0.2704.79 Safari/537.36 Edge/14.14393",
        "Mozilla/4.0 (compatible; MSIE 8.0; Windows NT 5.1; Trident/4.0; .NET CLR 1.1.4322; .NET CLR 2.0.50727; .NET CLR 3.0.4506.2152; .NET CLR 3.5.30729)",
        "Mozilla/5.0 (iPad; CPU OS 8_4_1 like Mac OS X) AppleWebKit/600.1.4 (KHTML, like Gecko) Version/8.0 Mobile/12H321 Safari/600.1.4",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 10_3_1 like Mac OS X) AppleWebKit/603.1.30 (KHTML, like Gecko) Version/10.0 Mobile/14E304 Safari/602.1",
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        "Mozilla/5.0 (Linux; Android 5.0; SAMSUNG SM-N900 Build/LRX21V) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/2.1 Chrome/34.0.1847.76 Mobile Safari/537.36",
        "Mozilla/5.0 (Linux; Android 6.0.1; SAMSUNG SM-G570Y Build/MMB29K) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/4.0 Chrome/44.0.2403.133 Mobile Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:53.0) Gecko/20100101 Firefox/53.0"
    ])



def fix_relative(html, url):

    """ this is fucking cheesy """
    
    
    try:
        base = "/".join(url.split("/")[:3])

        html = html.replace("src='//", "src='http://")
        html = html.replace('src="//', 'src="http://')


        html = html.replace("src='/", "src='%s/" % base)
        html = html.replace('src="/', 'src="%s/' % base)


        html = html.replace("href='//", "href='http://")
        html = html.replace('href="//', 'href="http://')


        html = html.replace("href='/", "href='%s/" % base)
        html = html.replace('href="/', 'href="%s/' % base)


    
    except Exception as ex:
        pass    

    return html
        

def update_feeds(max_feeds=3, output=NullOutput()):


    todo = Source.objects.filter(Q(due_poll__lt = timezone.now()) & Q(live = True))

    
    output.write("Queue size is {}".format(todo.count()))

    sources = todo.order_by("due_poll")[:max_feeds]

    output.write("\nProcessing %d\n\n" % sources.count())


    for src in sources:
        read_feed(src, output)
        
    # kill shit proxies
    
    WebProxy.objects.filter(address='X').delete()
    
    
def read_feed(source_feed, output=NullOutput()):

    old_interval = source_feed.interval


    was302 = False
    
    output.write("\n------------------------------\n")
    
    source_feed.last_polled = timezone.now()
    
    agent = get_agent(source_feed)

    headers = { "User-Agent": agent } #identify ourselves 


    

    proxies = {}
    proxy = None
    
    feed_url = source_feed.feed_url
    if source_feed.is_cloudflare : # Fuck you !
    

        if settings.FEEDS_CLOUDFLARE_WORKER:
            feed_url = "{}/read/?target={}".format(settings.FEEDS_CLOUDFLARE_WORKER, feed_url)
        else:
            try:
                proxy = get_proxy(output)
            
                if proxy.address != "X":
            
                    proxies = {
                      'http': proxy.address,
                      'https': proxy.address,
                    }
            except:
                pass    


    if source_feed.etag:
        headers["If-None-Match"] = str(source_feed.etag)
    if source_feed.last_modified:
        headers["If-Modified-Since"] = str(source_feed.last_modified)

    output.write("\nFetching %s" % feed_url)
    
    ret = None
    try:
        ret = requests.get(feed_url, headers=headers, verify=False, allow_redirects=False, timeout=20, proxies=proxies)
        source_feed.status_code = ret.status_code
        source_feed.last_result = "Unhandled Case"
        output.write(str(ret))
    except Exception as ex:
        source_feed.last_result = ("Fetch error:" + str(ex))[:255]
        source_feed.status_code = 0
        output.write("\nFetch error: " + str(ex))


        if proxy:
            source_feed.last_result = "Proxy failed. Next retry will use new proxy"
            source_feed.status_code = 1  # this will stop us increasing the interval

            output.write("\nBurning the proxy.")
            proxy.delete()
            source_feed.interval /= 2


        
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

            if source_feed.is_cloudflare and proxy is not None:
                # we are already proxied - this proxy on cloudflare's shit list too?
                proxy.delete()
                output.write("\nProxy seemed to also be blocked, burning")
                source_feed.interval /= 2
                source_feed.last_result = "Proxy kind of worked but still got cloudflared."
            else:            
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
                source_feed.save(update_fields=["feed_url", "last_result"])


            else:
                source_feed.last_result = "Feed has moved but no location provided"
        except exception as Ex:
            output.write("\nError redirecting.")
            source_feed.last_result = ("Error redirecting feed to " + new_url)[:255] 
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
                
            
            ret = requests.get(new_url, headers=headers, allow_redirects=True, timeout=20, verify=False)
            source_feed.status_code = ret.status_code
            source_feed.last_result = ("Temporary Redirect to " + new_url)[:255]

            if source_feed.last_302_url == new_url:
                #this is where we 302'd to last time
                td = timezone.now() - source_feed.last_302_start
                if td.days > 60:
                    source_feed.feed_url = new_url
                    source_feed.last_302_url = " "
                    source_feed.last_302_start = None
                    source_feed.last_result = ("Permanent Redirect to " + new_url)[:255]

                    source_feed.save(update_fields=["feed_url", "last_result", "last_302_url", "last_302_start"])



                else:
                    source_feed.last_result = ("Temporary Redirect to " + new_url + " since " + source_feed.last_302_start.strftime("%d %B"))[:255]

            else:
                source_feed.last_302_url = new_url
                source_feed.last_302_start = timezone.now()

                source_feed.last_result = ("Temporary Redirect to " + new_url + " since " + source_feed.last_302_start.strftime("%d %B"))[:255]


        except Exception as ex:     
            source_feed.last_result = ("Failed Redirection to " + new_url +  " " + str(ex))[:255]
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

        (ok,changed) = import_feed(source_feed=source_feed, feed_body=ret.content, content_type=content_type, output=output)
        
        if ok and changed:
            source_feed.interval /= 2
            source_feed.last_result = " OK (updated)" #and temporary redirects
            source_feed.last_change = timezone.now()
            
        elif ok:
            source_feed.last_result = " OK"
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
    source_feed.save(update_fields=[
                "due_poll", "interval", "last_result", 
                "last_modified", "etag", "last_302_start", 
                "last_302_url", "last_success", "live", 
                "status_code", "max_index", "is_cloudflare",
                "last_change",
            ])
        

def import_feed(source_feed, feed_body, content_type, output=NullOutput()):


    ok = False
    changed = False
    
    if "xml" in content_type or feed_body[0:1] == b"<":
        (ok,changed) = parse_feed_xml(source_feed, feed_body, output)
    elif "json" in content_type or feed_body[0:1] == b"{":
        (ok,changed) = parse_feed_json(source_feed, str(feed_body, "utf-8"), output)
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
            p.save(update_fields=["index"])
            
        source_feed.max_index = idx
    
    return (ok, changed)
    

    
def parse_feed_xml(source_feed, feed_content, output):

    ok = True
    changed = False 
    
    if source_feed.posts.all().count() == 0:
        is_first = True
    else:
        is_first = False

    #output.write(ret.content)           
    try:
        
        _customize_sanitizer(parser)
        f = parser.parse(feed_content) #need to start checking feed parser errors here
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
        
    source_feed.save(update_fields=["last_success", "last_result"])
    
    if ok:
        try:
            source_feed.name = f.feed.title
            source_feed.save(update_fields=["name"])
        except Exception as ex:
            output.write("\nUpdate name error:" + str(ex))
            pass

        try:
            source_feed.site_url = f.feed.link
            source_feed.save(update_fields=["site_url"])
        except Exception as ex:
            pass
    

        try:
            source_feed.image_url = f.feed.image.href
            source_feed.save(update_fields=["image_url"])
        except:
            pass


        # either of these is fine, prefer description over summary
        # also feedparser will give us itunes:summary etc if there
        try:
            source_feed.description = f.feed.summary
        except:
            pass

        try:
            source_feed.description = f.feed.description
        except:
            pass

        try:
            source_feed.save(update_fields=["description"])
        except:
            pass


        #output.write(entries)
        entries.reverse() # Entries are typically in reverse chronological order - put them in right order
        for e in entries:
        

            # we are going to take the longest
            body = ""
            
            if hasattr(e, "content"):
                for c in e.content:
                    if len(c.value) > len(body):
                        body = c.value
            
            if hasattr(e, "summary"):
                if len(e.summary) > len(body):
                    body = e.summary

            if hasattr(e, "summary_detail"):
                if len(e.summary_detail.value) > len(body):
                    body = e.summary_detail.value

            if hasattr(e, "description"):
                if len(e.description) > len(body):
                    body = e.description


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
                p = Post(index=0, body=" ", title="", guid=guid)
                p.found = timezone.now()
                changed = True


                try:
                    p.created  = datetime.datetime.fromtimestamp(time.mktime(e.published_parsed)).replace(tzinfo=timezone.utc)
                except Exception as ex2:
                    try:
                        p.created  = datetime.datetime.fromtimestamp(time.mktime(e.updated_parsed)).replace(tzinfo=timezone.utc)
                    except Exception as ex3:
                        output.write("CREATED ERROR:" + str(ex3))
                        p.created  = timezone.now()


                p.source = source_feed
                p.save()
    
            try:
                p.title = e.title
                p.save(update_fields=["title"])
            except Exception as ex:
                output.write("Title error:" + str(ex))
                            
            try:
                p.link = e.link
                p.save(update_fields=["link"])
            except Exception as ex:
                output.write("Link error:" + str(ex))

            try:
                p.image_url = e.image.href
                p.save(update_fields=["image_url"])
            except:
                pass


        
            try:
                p.author = e.author
                p.save(update_fields=["author"])
            except Exception as ex:
                p.author = ""



            try:
                p.body = body                          
                p.save(update_fields=["body"])
                # output.write(p.body)
            except Exception as ex:
                output.write(str(ex))
                output.write(p.body)                

            
            try:
                seen_files = []
                
                post_files = e["enclosures"]
                non_dupes = []
                
                # find any files in media_content that aren't already declared as enclosures
                if "media_content" in e:
                    for ee in e["media_content"]:
                        found = False
                        for ff in post_files:
                            if ff["href"] == ee["url"]:
                                found = True
                                break
                        if not found:
                            non_dupes.append(ee)
                        
                    post_files += non_dupes
                
                
                for ee in list(p.enclosures.all()):
                    # check existing enclosure is still there
                    found_enclosure = False
                    for pe in post_files:
                    
                        href = "href"
                        if href not in pe:
                            href = "url"

                        length = "length"
                        if length not in pe:
                            length = "filesize"
                    
                    
                        if pe[href] == ee.href and ee.href not in seen_files:
                            found_enclosure = True
                        
                            try:
                                ee.length = int(pe[length])
                            except:
                                ee.length = 0

                            try:
                                type = pe["type"]
                            except:
                                type = "audio/mpeg"  # we are assuming podcasts here but that's probably not safe

                            ee.type = type

                            
                            if "medium" in pe:
                                ee.medium = pe["medium"]
                            
                            if "description" in pe:
                                ee.description = pe["description"][:512]
                                
                            
                            ee.save()
                            break
                    if not found_enclosure:
                        ee.delete()
                    seen_files.append(ee.href)
    
                for pe in post_files:

                    href = "href"
                    if href not in pe:
                        href = "url"

                    length = "length"
                    if length not in pe:
                        length = "filesize"

                    try:
                        if pe[href] not in seen_files:
                    
                            try:
                                length = int(pe[length])
                            except:
                                length = 0
                            
                            try:
                                type = pe["type"]
                            except:
                                type = "audio/mpeg"
                    
                            ee = Enclosure(post=p, href=pe[href], length=length, type=type)

                            if "medium" in pe:
                                ee.medium = pe["medium"]
                            
                            if "description" in pe:
                                ee.description = pe["description"][:512]



                            ee.save()
                    except Exception as ex:
                        pass
            except Exception as ex:
                if output:
                    output.write("No enclosures - " + str(ex))


    if is_first and source_feed.posts.all().count() > 0:
        # If this is the first time we have parsed this 
        # then see if it's paginated and go back through its history
        agent = get_agent(source_feed)
        headers = { "User-Agent": agent } #identify ourselves 
        keep_going = True
        while keep_going:
            keep_going = False  # assume were stopping unless we find a next link     
            if hasattr(f.feed, 'links'): 
                for link in f.feed.links: 
                    if 'rel' in link and link['rel'] == "next":
                        ret = requests.get(link['href'], headers=headers, verify=False, allow_redirects=True, timeout=20)
                        (pok, pchanged) = parse_feed_xml(source_feed, ret.content, output)
                        # print(link['href'])
                        # print((pok, pchanged))
                        f = parser.parse(ret.content)  # rebase the loop on this feed version
                        keep_going = True
            

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

        source_feed.save(update_fields=["last_success", "last_result"])


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
            return (False, False, source_feed.interval)

        try:
            source_feed.site_url = f["home_page_url"]
            source_feed.name = f["title"]

            source_feed.save(update_fields=["site_url", "title"])

        except Exception as ex:
            pass


        try:
            if "description" in f:
                _customize_sanitizer(parser)
                source_feed.description = parser.sanitizer._sanitize_html(f["description"], "utf-8", 'text/html')
                source_feed.save(update_fields=["description"])
        except Exception as ex:
            pass
                    
        try:
            _customize_sanitizer(parser)
            source_feed.name = parser.sanitizer._sanitize_html(source_feed.name, "utf-8", 'text/html')
            source_feed.save(update_fields=["name"])

        except Exception as ex:
            pass

        try:
            if "icon" in f:
                source_feed.image_url = f["icon"]
                source_feed.save(update_fields=["icon"])
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
                p = Post(index=0, body=' ')
                p.found = timezone.now()
                changed = True
                p.source = source_feed
    
            try:
                title = e["title"]
            except Exception as ex:
                title = ""      
                
            # borrow the RSS parser's sanitizer
            _customize_sanitizer(parser)
            body = parser.sanitizer._sanitize_html(body, "utf-8", 'text/html') # TODO: validate charset ??
            _customize_sanitizer(parser)
            title = parser.sanitizer._sanitize_html(title, "utf-8", 'text/html') # TODO: validate charset ??
            # no other fields are ever marked as |safe in the templates

            if "banner_image" in e:
                p.image_url = e["banner_image"]                

            if "image" in e:
                p.image_url = e["image"]                

                        
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
                
            p.save()
            

            try:
                seen_files = []
                for ee in list(p.enclosures.all()):
                    # check existing enclosure is still there
                    found_enclosure = False
                    if "attachments" in e:
                        for pe in e["attachments"]:
                    
                            if pe["url"] == ee.href and ee.href not in seen_files:
                                found_enclosure = True
                        
                                try:
                                    ee.length = int(pe["size_in_bytes"])
                                except:
                                    ee.length = 0

                                try:
                                    type = pe["mime_type"]
                                except:
                                    type = "audio/mpeg"  # we are assuming podcasts here but that's probably not safe

                                ee.type = type
                                ee.save()
                                break
                    if not found_enclosure:
                        ee.delete()
                    seen_files.append(ee.href)

                if "attachments" in e:
                    for pe in e["attachments"]:

                        try:
                            if pe["url"] not in seen_files:
                    
                                try:
                                    length = int(pe["size_in_bytes"])
                                except:
                                    length = 0
                            
                                try:
                                    type = pe["mime_type"]
                                except:
                                    type = "audio/mpeg"
                    
                                ee = Enclosure(post=p , href=pe["url"], length=length, type=type)
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
                output.write(str(ex))
                output.write(p.body)

    return (ok,changed)
    
    
def test_feed(source, cache=False, output=NullOutput()):


    headers = { "User-Agent": get_agent(source)  } #identify ourselves and also stop our requests getting picked up by any cache

    if cache:
        if source.etag:
            headers["If-None-Match"] = str(source.etag)
        if source.last_modified:
            headers["If-Modified-Since"] = str(source.last_modified)
    else:
        headers["Cache-Control"] = "no-cache,max-age=0" 
        headers["Pragma"] = "no-cache"

    output.write("\n" + str(headers))

    ret = requests.get(source.feed_url, headers=headers, allow_redirects=False, verify=False, timeout=20)

    output.write("\n\n")
    
    output.write(str(ret))
    
    output.write("\n\n")
    
    output.write(ret.text)
    
    
def get_proxy(out=NullOutput()):

    p = WebProxy.objects.first()
    
    if p is None:
        find_proxies(out)
        p = WebProxy.objects.first()
    
    out.write("Proxy: {}".format(str(p)))
    
    return p 
    
    

def find_proxies(out=NullOutput()):
    
    
    out.write("\nLooking for proxies\n")
    
    try:
        req = requests.get("https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list.txt", timeout=30)
        if req.status_code == 200:
            list = req.text
            
            list = list.split("\n")
            
            # remove header
            list = list[4:]
            
            for item in list:
                if ":" in item:
                    item = item.split(" ")[0]
                    WebProxy(address=item).save()


                        
    except Exception as ex:
        logging.error("Proxy scrape error: {}".format(str(ex)))
        out.write("Proxy scrape error: {}\n".format(str(ex)))
            
    if WebProxy.objects.count() == 0:
        # something went wrong.
        # to stop infinite loops we will insert duff proxys now
        for i in range(20):
            WebProxy(address="X").save()
        out.write("No proxies found.\n")
    
