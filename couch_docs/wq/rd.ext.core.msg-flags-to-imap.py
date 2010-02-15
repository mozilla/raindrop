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

# An extension which converts message flags to a schema processed by the
# imap protocol to reflect the new flags back to the imap server.

def handler(doc):
    # Our source schema is also written as the message is incoming, so
    # skip messages not destined to be sent.
    if doc['outgoing_state'] != 'outgoing':
        return

    # query a view to find out what the folder and UID of the item is.
    rdkey = doc['rd_key']
    key = ["key-schema_id", [rdkey, 'rd.msg.location']]
    result = open_view(key=key, reduce=False, include_docs=True)
    # A single message may appear in multiple places...
    for row in result['rows']:
        # Check it really is for an IMAP account.  The 'source' for imap
        # accounts is ['imap', acct_name]
        loc_doc = row['doc']
        if loc_doc['source'][0] != 'imap':
            logger.info('outgoing item not for imap acct (source is %r)',
                        loc_doc['source'])
            continue
        # It is for IMAP - write a schema with the flags adjustments...
        folder = loc_doc.get('location_sep', '/').join(loc_doc['location'])
        uid = loc_doc['uid']
        logger.debug("setting flags for %r: folder %r, uuid %s", rdkey, folder, uid)

        if doc['rd_schema_id'] == 'rd.msg.seen':
            new_flag = '\\Seen'
            attr = 'seen'
        elif doc['rd_schema_id'] == 'rd.msg.deleted':
            new_flag = '\\Deleted'
            attr = 'deleted'
        elif doc['rd_schema_id'] == 'rd.msg.archived':
            logger.info("todo: ignoring 'archived' IMAP flag")
            continue
        else:
            raise RuntimeError(doc)
        items = {'account': loc_doc['source'][1],
                 'folder': folder,
                 'uid': uid,}
        if doc[attr]:
            items['flags_add']=[new_flag]
        else:
            items['flags_remove']=[new_flag]

        emit_schema('rd.proto.outgoing.imap-flags', items)
