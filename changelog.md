
### 1.1.0
- Add ability to manage read status, put feeds in folders, have multiple users

### 1.0.9
- Decreases the size of GUID slightly, was not compatible with MySQL

### 1.0.8
- Increases the size of GUIDs
- Adds support for Django 5

### 1.0.7
- Fixes a bug that could result in the wrong body being set on items with a <content:encoded> element
- Separates the handling of 403 and 410 result codes

### 1.0.6
- Fixes a bug preventing existing enclosures being updated when re-reading a feed
- Adds support for the 'medium' attribute on  <content:encoded> items.

### 1.0.5
- Support feed pagination.  The first time a feed is read the parser will try to use pagination, if available, to get all available content

### 1.0.4
- Bug fix

### 1.0.3
- Support media:content.  Various bug fixes.

### 1.0.0
- No real changes, but this is fine.  No more 0.x nonsense

### 0.3.2
- bug fix release - `last_change` was not being saved

### 0.3.1
- Alternative method of busting cloudflare - using cloudflare workers!

### 0.2.0
- Admin improvements from Chris Spencer, does break reverse FK links so be careful.

### 0.1.2
- First really usable version
