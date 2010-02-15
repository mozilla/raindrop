# ***** BEGIN LICENSE BLOCK *****
# Version: MPL 1.1
#
# The contents of this file are subject to the Mozilla Public License Version
# 1.1 (the "License"); you may not use this file except in compliance with
# the License. You may obtain a copy of the License at
# http://www.mozilla.org/MPL/
#
# Software distributed under the License is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied. See the License
# for the specific language governing rights and limitations under the
# License.
#
# The Original Code is Raindrop.
#
# The Initial Developer of the Original Code is
# Mozilla Messaging, Inc..
# Portions created by the Initial Developer are Copyright (C) 2009
# the Initial Developer. All Rights Reserved.
#
# Contributor(s):
#

import feedparser
import time
from StringIO import StringIO

def entry_to_items(entry):
    items = {}
    detail_items = [
        ('title', ('title_detail', ('value', 'type', 'language', 'base'))),
        ('summary', ('summary_detail', ('value', 'type', 'language', 'base'))),
        ('content', ('content_detail', ('value', 'type', 'language', 'base'))),
        ('author', ('author_detail', ('name', 'href', 'detail', 'base')))
    ]

def make_feedparser_jsonable(ob):
    if isinstance(ob, dict):
        ret = {}
        for k, v in ob.iteritems():
            ret[k] = make_feedparser_jsonable(v)
    elif isinstance(ob, list):
        ret = [make_feedparser_jsonable(i) for i in ob]
    elif isinstance(ob, time.struct_time):
        ret = time.mktime(ob)
    else:
        ret = ob
    return ret

def handler(doc):
    if 'headers' not in doc:
        logger.info('skipping blank rss feed %(_id)r', doc)
        return

    # I need the binary attachment to send via feedparser
    content = open_schema_attachment(doc, "response")
    # we trick feedparser by giving it a file-like object which also
    # supplies a headers object.
    f = StringIO(content)
    headers = {}
    # turn back to single items.
    for name, vals in doc['headers'].iteritems():
        headers[name] = ';'.join(vals)
    # must provide a content-location for relative urls.
    if 'content-location' not in headers:
        headers['content-location'] = doc['uri']
    class FakeHeaders:
        get = headers.get
        dict = headers
    f.headers = FakeHeaders()

    info = feedparser.parse(f)
    # nuke and convert some of the channel info.
    info_values = info.copy()
    del info_values['entries']
    info_values = make_feedparser_jsonable(info_values)

    entries = info.entries
    logger.info("feed %r has %d items total", doc['uri'], len(entries))
    # for now each item, see what items are new.
    entries_by_rdkey = {}
    keys = []
    for entry in entries:
        guid = getattr(entry, 'guid', None) or getattr(entry, 'link', None)
        if not guid:
            logger.info("can't work out an rd_key for rss entry %s", entry)
            continue
        rd_key = ['rss-entry', guid]
        entries_by_rdkey[tuple(rd_key)] = entry
        keys.append(['key-schema_id', [rd_key, 'rd.raw.rss-entry']])

    # query for existing items
    existing_by_rdkey = {}
    result = open_view(keys=keys, reduce=False, include_docs=True)
    for row in result['rows']:
        existing_by_rdkey[tuple(row['value']['rd_key'])] = row['doc']

    num = 0
    for rd_key, entry in entries_by_rdkey.iteritems():
        try:
            existing = existing_by_rdkey[rd_key]
            # XXX - should we check the content is the same???
            logger.debug('rss item %r already exists - skipping', rd_key)
        except KeyError:
            rd_key = list(rd_key)
            logger.info('creating rss-entry %r', rd_key)
            items = make_feedparser_jsonable(entry)
            items['channel'] = info_values
            items['timestamp'] = items['updated_parsed']
            emit_schema('rd.raw.rss-entry', items, rd_key=rd_key)
            # and also emit a grouping-tag - it probably should be its own
            # extension, but really it is too trivial to bother with...
            gt = {'tag': "-".join(doc['rd_key'])}
            emit_schema('rd.msg.grouping-tag', gt, rd_key=rd_key)

            num += 1
    logger.info('created %d rss items from %r', num, doc['_id'])
