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

# Emit rd.msg.grouping-tag schemas for emails.

def handler(src_doc):
    # We make our lives easier by using the rd.msg.body schema, but only
    # do emails...
    if src_doc['rd_key'][0] != 'email':
        return

    my_identities = get_my_identities()
    to = src_doc.get('to', [])
    val = None
    # First check if any of the identities is marked as being 'broadcast' or
    # 'group'
    from_id = src_doc.get('from')
    if from_id:
        # We must mark the identity's msg-grouping-tag as a dependency - even
        # when it doesn't yet exist - it may be created later, at which time
        # we need to be re-executed.
        idty_rdkey = ['identity', from_id]
        deps = [(idty_rdkey, 'rd.identity.msg-grouping-tag')]
        id_schema = open_schemas(deps)[0] # will be None if it doesn't exist
    else:
        id_schema = None
        deps = None
    if id_schema:
        val = id_schema['tag']
    elif src_doc.get('from') in my_identities:
        val = 'from'
    # only us on the 'to' line?  That is 'direct'...
    elif len(to)==1 and to[0] in my_identities:
        val = 'direct'
    else:
        look = to + src_doc.get('cc', [])
        for maybe in look:
            if maybe in my_identities:
                val = 'group'
                break
        else:
            val = 'broadcast'
    items = {'tag' : val,
            }
    emit_schema('rd.msg.grouping-tag', items, deps=deps)
