from django.test import TestCase, Client

# Create your tests here.
from feeds.models import Source, Post, Enclosure
from feeds.utils import read_feed

from django.utils import timezone
from django.urls import reverse

from datetime import timedelta

import mock

import os

import requests_mock

TEST_FILES_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)),"testdata")
BASE_URL = 'http://feed.com/'

class BaseTest(TestCase):


    def _populate_mock(self, mock, test_file, status, content_type, etag=None, headers=None, url=BASE_URL):
    
        content = open(os.path.join(TEST_FILES_FOLDER, test_file), "rb").read()
        
        
        ret_headers =  {"Content-Type": content_type, "etag":"an-etag"}
        if headers is not None:
            ret_headers = {**ret_headers, **headers}
        
        {"Content-Type": content_type, "etag":"an-etag"}
        
        if etag is None:
            mock.register_uri('GET', url, status_code=status, content=content, headers=ret_headers)
        else:
            mock.register_uri('GET', url, request_headers={'If-None-Match': etag}, status_code=status, content=content, headers=ret_headers)


@requests_mock.Mocker()
class XMLFeedsTest(BaseTest):


    def test_simple_xml(self, mock):
        
        self._populate_mock(mock, status=200, test_file="rss_xhtml_body.xml", content_type="application/rss+xml")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()
        
        # Read the feed once to get the 1 post  and the etag
        read_feed(src)         
        self.assertEqual(src.status_code, 200)
        self.assertEqual(src.post_set.count(), 1) # got the one post
        self.assertEqual(src.interval, 60)
        self.assertEqual(src.etag, "an-etag")


    def test_sanitize(self, mock):
        
        self._populate_mock(mock, status=200, test_file="rss_xhtml_body.xml", content_type="application/rss+xml")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()
        
        # Read the feed once to get the 1 post  and the etag
        read_feed(src)         
        self.assertEqual(src.status_code, 200)
        p = src.post_set.all()[0]
        
        self.assertFalse("<script>" in p.body)


@requests_mock.Mocker()
class JSONFeedTest(BaseTest):


    def test_simple_json(self, mock):
        
        self._populate_mock(mock, status=200, test_file="json_simple_two_entry.json", content_type="application/json")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()
        
        # Read the feed once to get the 1 post  and the etag
        read_feed(src)         
        self.assertEqual(src.status_code, 200)
        self.assertEqual(src.post_set.count(), 2) # got the one post
        self.assertEqual(src.interval, 60)
        self.assertEqual(src.etag, "an-etag")
        

    def test_sanitize(self, mock):
        
        self._populate_mock(mock, status=200, test_file="json_simple_two_entry.json", content_type="application/json")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()
        
        # Read the feed once to get the 1 post  and the etag
        read_feed(src)         
        self.assertEqual(src.status_code, 200)
        p = src.post_set.all()[0]
        
        self.assertFalse("<script>" in p.body)     


@requests_mock.Mocker()
class HTTPStuffTest(BaseTest):


    def test_etags(self, mock):

        self._populate_mock(mock, status=200, test_file="rss_xhtml_body.xml", content_type="application/xml+rss")
        self._populate_mock(mock, status=304, test_file="empty_file.txt", content_type="application/xml+rss", etag="an-etag")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()
        
        # Read the feed once to get the 1 post  and the etag
        read_feed(src)         
        self.assertEqual(src.status_code, 200)
        self.assertEqual(src.post_set.count(), 1) # got the one post
        self.assertEqual(src.interval, 60)
        self.assertEqual(src.etag, "an-etag")

        # Read the feed again to get a 304 and a small increment to the interval
        read_feed(src)         
        self.assertEqual(src.post_set.count(), 1) # should have no more
        self.assertEqual(src.status_code, 304)
        self.assertEqual(src.interval, 70)
        self.assertTrue(src.live)
        

    def test_not_a_feed(self, mock):
             
        self._populate_mock(mock, status=200, test_file="spurious_text_file.txt", content_type="text/plain")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()
        
        read_feed(src)         
        self.assertEqual(src.status_code, 200)  # it returned a page, but not a  feed
        self.assertEqual(src.post_set.count(), 0) # can't have got any
        self.assertEqual(src.interval, 120)
        self.assertTrue(src.live)


    def test_permission_denied(self, mock):
             
        self._populate_mock(mock, status=403, test_file="empty_file.txt", content_type="text/plain")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()
        
        read_feed(src)         
        self.assertEqual(src.status_code, 403)  # it returned a page, but not a  feed
        self.assertEqual(src.post_set.count(), 0) # can't have got any
        self.assertFalse(src.live)
        

    def test_feed_gone(self, mock):
             
        self._populate_mock(mock, status=410, test_file="empty_file.txt", content_type="text/plain")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()
        
        read_feed(src)         
        self.assertEqual(src.status_code, 410)  # it returned a page, but not a  feed
        self.assertEqual(src.post_set.count(), 0) # can't have got any
        self.assertFalse(src.live)

    def test_feed_not_found(self, mock):
             
        self._populate_mock(mock, status=404, test_file="empty_file.txt", content_type="text/plain")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()
        
        read_feed(src)         
        self.assertEqual(src.status_code, 404)  # it returned a page, but not a  feed
        self.assertEqual(src.post_set.count(), 0) # can't have got any
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
        self.assertEqual(src.status_code, 200)  
        self.assertEqual(src.last_302_url, new_url)  # this is where  went
        self.assertIsNotNone(src.last_302_start)
        self.assertEqual(src.post_set.count(), 1) # after following redirect will have 1 post
        self.assertEqual(src.interval, 60)
        self.assertTrue(src.live)

        # do it all again -  shouldn't change
        read_feed(src)         
        self.assertEqual(src.status_code, 200)  # it returned a page, but not a  feed
        self.assertEqual(src.last_302_url, new_url)  # this is where  went
        self.assertIsNotNone(src.last_302_start)
        self.assertEqual(src.post_set.count(), 1) # after following redirect will have 1 post
        self.assertEqual(src.interval, 80)
        self.assertTrue(src.live)

        
        # now we test making it permaent
        src.last_302_start = timezone.now() - timedelta(days=365)
        src.save()
        read_feed(src)         
        self.assertEqual(src.status_code, 200)  
        self.assertEqual(src.last_302_url, ' ')  
        self.assertIsNone(src.last_302_start)
        self.assertEqual(src.post_set.count(), 1) 
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
        self.assertEqual(src.status_code, 301)  
        self.assertEqual(src.interval, 60)  

        read_feed(src)         
        self.assertEqual(src.status_code, 200)  
        self.assertEqual(src.post_set.count(), 1) 
        self.assertEqual(src.interval, 60)
        self.assertEqual(src.feed_url, new_url)
        self.assertTrue(src.live)


    def test_server_error_1(self, mock):
             
        self._populate_mock(mock, status=500, test_file="empty_file.txt", content_type="text/plain")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()
        
        read_feed(src)         
        self.assertEqual(src.status_code, 500)  # it returned a page, but not a  feed
        self.assertEqual(src.post_set.count(), 0) # can't have got any
        self.assertTrue(src.live)       
        self.assertEqual(src.interval, 120)
        

    def test_server_error_2(self, mock):
             
        self._populate_mock(mock, status=503, test_file="empty_file.txt", content_type="text/plain")

        src = Source(name="test1", feed_url=BASE_URL, interval=0)
        src.save()
        
        read_feed(src)         
        self.assertEqual(src.status_code, 503)  # it returned a page, but not a  feed
        self.assertEqual(src.post_set.count(), 0) # can't have got any
        self.assertTrue(src.live)       
        self.assertEqual(src.interval, 120)
 
        
