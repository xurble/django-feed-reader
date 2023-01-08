from django.test import TestCase, Client
from django.conf import settings

# Create your tests here.
from feeds.models import Source, Post, Enclosure, WebProxy
from feeds.utils import read_feed, find_proxies, get_proxy, fix_relative

from django.utils import timezone
from django.urls import reverse

from datetime import timedelta

import mock

import os

import requests_mock

TEST_FILES_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)),"testdata")
BASE_URL = 'http://feed.com/'

class UtilsTest(TestCase):


    def test_fix_relative(self):
    
        url = "https://example.com/rss.xml"
        html= "<a href='/'><img src='/image.jpg'></a>"
    
        html = fix_relative(html, url)
        
        self.assertEqual(html, "<a href='https://example.com/'><img src='https://example.com/image.jpg'></a>")
        


class BaseTest(TestCase):


    def _populate_mock(self, mock, test_file, status, content_type, etag=None, headers=None, url=BASE_URL, is_cloudflare=False):
    
        content = open(os.path.join(TEST_FILES_FOLDER, test_file), "rb").read()
        
        
        ret_headers =  {"Content-Type": content_type, "etag":"an-etag"}
        if headers is not None:
            ret_headers = {**ret_headers, **headers}
        
        {"Content-Type": content_type, "etag":"an-etag"}
        
        if is_cloudflare:
            agent = "{user_agent} (+{server}; Updater; {subs} subscribers)".format(user_agent=settings.FEEDS_USER_AGENT, server=settings.FEEDS_SERVER, subs=1)

            mock.register_uri('GET', url, request_headers={"User-Agent": agent}, status_code=status, content=content, headers=ret_headers)
        else:
            if etag is None:
                mock.register_uri('GET', url, status_code=status, content=content, headers=ret_headers)
            else:
                mock.register_uri('GET', url, request_headers={'If-None-Match': etag}, status_code=status, content=content, headers=ret_headers)
                    


@requests_mock.Mocker()
class XMLFeedsTest(BaseTest):


    def test_simple_xml(self, mock):
        
        self._populate_mock(mock, status=200, test_file="rss_xhtml_body.xml", content_type="application/rss+xml")



        ls = timezone.now()

        src = Source(name="test1", feed_url=BASE_URL, interval=0, last_success=ls, last_change=ls)
        src.save()
        
        # Read the feed once to get the 1 post  and the etag
        read_feed(src)
        src.refresh_from_db()
         
        self.assertEqual(src.status_code, 200)
        self.assertEqual(src.posts.count(), 1) # got the one post
        self.assertEqual(src.interval, 60)
        self.assertEqual(src.etag, "an-etag")
        self.assertNotEqual(src.last_success, ls)
        self.assertNotEqual(src.last_change, ls)


    def test_podcast(self, mock):
    
        self._populate_mock(mock, status=200, test_file="podcast.xml", content_type="application/rss+xml")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()
        
        # Read the feed once to get the 1 post  and the etag
        read_feed(src)
        src.refresh_from_db()
        
        
        self.assertEqual(src.description, 'SU: Three nerds discussing tech, Apple, programming, and loosely related matters.') 

        self.assertEqual(src.posts.all()[0].enclosures.count(), 1)

    def test_mastodon(self, mock):
    
        self._populate_mock(mock, status=200, test_file="mastodon.xml", content_type="application/rss+xml")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()
        
        # Read the feed once to get the 1 post  and the etag

        read_feed(src)
        src.refresh_from_db()
        
        
        self.assertEqual(src.description, 'Public posts from @xurble@toot.community') 


        self.assertEqual(src.posts.all()[0].enclosures.count(), 1)
        
        




    def test_sanitize_1(self, mock):
    
        """
            Make sure feedparser's sanitization is running
        """
        
        self._populate_mock(mock, status=200, test_file="rss_xhtml_body.xml", content_type="application/rss+xml")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()
        
        # Read the feed once to get the 1 post  and the etag
        read_feed(src)
        src.refresh_from_db()
         
        self.assertEqual(src.status_code, 200)
        p = src.posts.all()[0]
        
        self.assertFalse("<script>" in p.body)
        
        
    def test_sanitize_2(self, mock): 
        """
            Another test that the sanitization is going on.  This time we have 
            stolen a test case from the feedparser libarary
        """
    
        self._populate_mock(mock, status=200, test_file="sanitizer_bad_comment.xml", content_type="application/rss+xml")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()
        
        # read the feed to update the name
        read_feed(src)
        src.refresh_from_db()
         
        self.assertEqual(src.status_code, 200)
        self.assertEqual(src.name, "safe")
        
    
    def test_sanitize_attrs(self, mock):

        self._populate_mock(mock, status=200, test_file="sanitizer_img_attrs.xml", content_type="application/rss+xml")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()
        
        # read the feed to update the name
        read_feed(src)
        src.refresh_from_db()
         
        self.assertEqual(src.status_code, 200)

        body = src.posts.all()[0].body
        
        self.assertTrue("<img" in body)
        self.assertFalse("align=" in body)
        self.assertFalse("hspace=" in body)
        


@requests_mock.Mocker()
class JSONFeedTest(BaseTest):


    def test_simple_json(self, mock):
        
        self._populate_mock(mock, status=200, test_file="json_simple_two_entry.json", content_type="application/json")

        ls = timezone.now()

        src = Source(name="test1", feed_url=BASE_URL, interval=0, last_success=ls, last_change=ls)
        src.save()
        
        # Read the feed once to get the 1 post  and the etag
        read_feed(src)
        src.refresh_from_db()
         
        self.assertEqual(src.status_code, 200)
        self.assertEqual(src.posts.count(), 2) # got the one post
        self.assertEqual(src.interval, 60)
        self.assertEqual(src.etag, "an-etag")
        self.assertNotEqual(src.last_success, ls)
        self.assertNotEqual(src.last_change, ls)
        

    def test_sanitize_1(self, mock):
        
        self._populate_mock(mock, status=200, test_file="json_simple_two_entry.json", content_type="application/json")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()
        
        # Read the feed once to get the 1 post  and the etag
        read_feed(src)
        src.refresh_from_db()
         
        self.assertEqual(src.status_code, 200)
        p = src.posts.all()[0]
        
        self.assertFalse("<script>" in p.body)     

    def test_sanitize_2(self, mock): 
        """
            Another test that the sanitization is going on.  This time we have 
            stolen a test case from the feedparser libarary
        """
    
        self._populate_mock(mock, status=200, test_file="sanitizer_bad_comment.json", content_type="application/json")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()
        
        # read the feed to update the name
        read_feed(src)
        src.refresh_from_db()
         
        self.assertEqual(src.status_code, 200)
        self.assertEqual(src.name, "safe")
        
    
    def test_podcast(self, mock):

        self._populate_mock(mock, status=200, test_file="podcast.json", content_type="application/json")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()
        
        # read the feed to update the name
        read_feed(src)
        src.refresh_from_db()
         
        self.assertEqual(src.status_code, 200)
        
        post = src.posts.all()[0]
        
        self.assertEqual(post.enclosures.count(), 1)


@requests_mock.Mocker()
class HTTPStuffTest(BaseTest):

    def test_fucking_cloudflare(self, mock):

        self._populate_mock(mock, status=200, test_file="json_simple_two_entry.json", content_type="application/json")
        self._populate_mock(mock, status=403, test_file="json_simple_two_entry.json", content_type="application/json", is_cloudflare=True)

        src = Source(name="test1", feed_url=BASE_URL, interval=0, is_cloudflare=False)
        src.save()
        
        # Read the feed once to get the 1 post  and the etag
        read_feed(src)
        src.refresh_from_db()
         
        self.assertEqual(src.status_code, 403)

        src = Source(name="test1", feed_url=BASE_URL, interval=0, is_cloudflare=True)
        src.save()
        
        # Read the feed once to get the 1 post  and the etag
        read_feed(src)
        src.refresh_from_db()
         
        self.assertEqual(src.status_code, 200)
        
    def test_find_proxies(self, mock):

        self._populate_mock(mock, status=200, test_file="proxy_list.html", content_type="text/html", url="http://www.workingproxies.org")
    
        find_proxies()
        
        self.assertEqual(WebProxy.objects.count(), 20)

    def test_get_proxy(self, mock):

        self._populate_mock(mock, status=200, test_file="proxy_list.html", content_type="text/html", url="http://www.workingproxies.org")
    
        p = get_proxy()
        
        self.assertIsNotNone(p)

    def test_etags(self, mock):

        self._populate_mock(mock, status=200, test_file="rss_xhtml_body.xml", content_type="application/xml+rss")
        self._populate_mock(mock, status=304, test_file="empty_file.txt", content_type="application/xml+rss", etag="an-etag")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()
        
        # Read the feed once to get the 1 post  and the etag
        read_feed(src)
        src.refresh_from_db()
         
        self.assertEqual(src.status_code, 200)
        self.assertEqual(src.posts.count(), 1) # got the one post
        self.assertEqual(src.interval, 60)
        self.assertEqual(src.etag, "an-etag")

        # Read the feed again to get a 304 and a small increment to the interval
        read_feed(src)
        src.refresh_from_db()
         
        self.assertEqual(src.posts.count(), 1) # should have no more
        self.assertEqual(src.status_code, 304)
        self.assertEqual(src.interval, 70)
        self.assertTrue(src.live)
        

    def test_not_a_feed(self, mock):
             
        self._populate_mock(mock, status=200, test_file="spurious_text_file.txt", content_type="text/plain")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()
        
        read_feed(src)
        src.refresh_from_db()
         
        self.assertEqual(src.status_code, 200)  # it returned a page, but not a  feed
        self.assertEqual(src.posts.count(), 0) # can't have got any
        self.assertEqual(src.interval, 120)
        self.assertTrue(src.live)


    def test_permission_denied(self, mock):
             
        self._populate_mock(mock, status=403, test_file="empty_file.txt", content_type="text/plain")

        ls = timezone.now()

        src = Source(name="test1", feed_url=BASE_URL, interval=0, last_success=ls)
        src.save()
        
        read_feed(src)
        src.refresh_from_db()
       
        
        
        
          
        self.assertEqual(src.status_code, 403)  # it returned a page, but not a  feed
        self.assertEqual(src.posts.count(), 0) # can't have got any
        self.assertFalse(src.live)
                

    def test_feed_gone(self, mock):
             
        self._populate_mock(mock, status=410, test_file="empty_file.txt", content_type="text/plain")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()
        
        read_feed(src)
        src.refresh_from_db()
         
        self.assertEqual(src.status_code, 410)  # it returned a page, but not a  feed
        self.assertEqual(src.posts.count(), 0) # can't have got any
        self.assertFalse(src.live)

    def test_feed_not_found(self, mock):
             
        self._populate_mock(mock, status=404, test_file="empty_file.txt", content_type="text/plain")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()
        
        read_feed(src)
        src.refresh_from_db()
         
        self.assertEqual(src.status_code, 404)  # it returned a page, but not a  feed
        self.assertEqual(src.posts.count(), 0) # can't have got any
        self.assertTrue(src.live)
        self.assertEqual(src.interval, 120)
        
    def test_temp_redirect(self, mock):
    
        new_url  = "http://new.feed.com/"
        self._populate_mock(mock, status=302, test_file="empty_file.txt", content_type="text/plain", headers={"Location": new_url})
        self._populate_mock(mock, status=200, test_file="rss_xhtml_body.xml", content_type="application/xml+rss",  url=new_url)

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()
        
        self.assertIsNone(src.last_302_start)
        
        
        read_feed(src)
        src.refresh_from_db()
         
        self.assertEqual(src.status_code, 200)  
        self.assertEqual(src.last_302_url, new_url)  # this is where  went
        self.assertIsNotNone(src.last_302_start)
        self.assertEqual(src.posts.count(), 1) # after following redirect will have 1 post
        self.assertEqual(src.interval, 60)
        self.assertTrue(src.live)

        # do it all again -  shouldn't change
        read_feed(src)
        src.refresh_from_db()
         
        self.assertEqual(src.status_code, 200)  # it returned a page, but not a  feed
        self.assertEqual(src.last_302_url, new_url)  # this is where  went
        self.assertIsNotNone(src.last_302_start)
        self.assertEqual(src.posts.count(), 1) # after following redirect will have 1 post
        self.assertEqual(src.interval, 80)
        self.assertTrue(src.live)

        
        # now we test making it permaent
        src.last_302_start = timezone.now() - timedelta(days=365)
        src.save()
        read_feed(src)
        src.refresh_from_db()
         
        self.assertEqual(src.status_code, 200)  
        self.assertEqual(src.last_302_url, ' ')  
        self.assertIsNone(src.last_302_start)
        self.assertEqual(src.posts.count(), 1) 
        self.assertEqual(src.interval, 100)
        self.assertEqual(src.feed_url, new_url)
        self.assertTrue(src.live)
        
        
    def test_perm_redirect(self, mock):

        new_url  = "http://new.feed.com/"
        self._populate_mock(mock, status=301, test_file="empty_file.txt", content_type="text/plain", headers={"Location": new_url})
        self._populate_mock(mock, status=200, test_file="rss_xhtml_body.xml", content_type="application/xml+rss",  url=new_url)

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()

        read_feed(src)
        src.refresh_from_db()
         
        self.assertEqual(src.status_code, 301)  
        self.assertEqual(src.interval, 60)  
        self.assertEqual(src.feed_url, new_url)

        read_feed(src)
        src.refresh_from_db()
         
        self.assertEqual(src.status_code, 200)  
        self.assertEqual(src.posts.count(), 1) 
        self.assertEqual(src.interval, 60)
        self.assertTrue(src.live)


    def test_server_error_1(self, mock):
             
        self._populate_mock(mock, status=500, test_file="empty_file.txt", content_type="text/plain")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()
        
        read_feed(src)
        src.refresh_from_db()
         
        self.assertEqual(src.status_code, 500)  # error
        self.assertEqual(src.posts.count(), 0) # can't have got any
        self.assertTrue(src.live)       
        self.assertEqual(src.interval, 120)
        

    def test_server_error_2(self, mock):
             
        self._populate_mock(mock, status=503, test_file="empty_file.txt", content_type="text/plain")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()
        
        read_feed(src)
        src.refresh_from_db()
         
        self.assertEqual(src.status_code, 503)  # error!
        self.assertEqual(src.posts.count(), 0) # can't have got any
        self.assertTrue(src.live)       
        self.assertEqual(src.interval, 120)
 
        
