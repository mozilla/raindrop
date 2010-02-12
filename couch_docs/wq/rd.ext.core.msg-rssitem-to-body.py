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

def handler(doc):
    ret = {}
    if 'author_detail' in doc:
        ad = doc['author_detail']
        if 'email' in ad:
            ret['from'] = ['email', ad['email'].lower()]
        if 'name' in ad:
            ret['from_display'] = ad['name']
    elif 'author' in doc:
        # don't have an identity-id for a from.
        ret['from_display'] = doc['author']

    if 'title_detail' in doc and 'value' in doc['title_detail']:
        ret['subject'] = doc['title_detail']['value']
    elif 'title' in doc:
        ret['subject'] = doc['title']

    # some rss entries may have only a 'title', but we want all schemas
    # of this type to have a 'body' field, even if blank.
    body_text = ""
    if 'summary_detail' in doc and 'value' in doc['summary_detail']:
        body_text = doc['summary_detail']['value']
    elif 'summary' in doc:
        body_text = summary
    # else body_text remains empty...
    # get rid of blank lines
    lines = [line.strip() for line in body_text.splitlines() if line.strip()]
    preview_body = "\n".join(lines)
    preview_body = preview_body[:140] + (preview_body[140:] and '...') # cute trick
    ret['body_preview'] = preview_body
    ret['body'] = body_text
    timestamp = 0
    if 'timestamp' in doc:
        ret['timestamp'] = timestamp = doc['timestamp']

    emit_schema('rd.msg.body', ret)
