#!/usr/bin/env python
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

'''
Fetch skype contacts and chats.
'''
import os
import time
import logging
import tempfile
from urllib import quote

from ..proc import base
brat = base.Rat

import Skype4Py

logger = logging.getLogger(__name__)

# These are the raw properties we fetch from skype.
CHAT_PROPS = [
    ('ACTIVEMEMBERS',   list),
    ('ACTIVITY_TIMESTAMP', float),
    ('ADDER', unicode),
    ('APPLICANTS', list),
    #'BLOB'??
    ('BOOKMARKED', bool),
    ('DESCRIPTION', unicode),
    #'DIALOG_PARTNER',
    ('FRIENDLYNAME', unicode),
    ('GUIDELINES', unicode),
    #'MEMBEROBJECTS',
    ('MEMBERS', list),
    ('MYROLE', unicode),
    ('MYSTATUS', unicode),
    ('OPTIONS', int),
    ('PASSWORDHINT', unicode),
    ('POSTERS', list),
    ('STATUS', unicode),
    ('TIMESTAMP', float),
    ('TOPICXML', unicode),
    ('TOPIC', unicode),
    ('TYPE', unicode),
]

MSG_PROPS = [
    ('BODY', unicode),
    ('CHATNAME', unicode),
    ('EDITED_BY', unicode),
    ('EDITED_TIMESTAMP', float),
    ('FROM_DISPNAME', unicode),
    ('FROM_HANDLE', unicode),
    ('IS_EDITABLE', bool),
    ('LEAVEREASON', unicode),
    ('STATUS', unicode),
    ('TIMESTAMP', float),
    ('TYPE', unicode),
    ('USERS', list),
]

def simple_convert(str_val, typ):
    if typ is list:
        return str_val.split()
    if typ is bool:
        return str_val == "TRUE"
    # all the rest are callables which 'do the right thing'
    return typ(str_val)


class TwistySkype(object):
    # The 'id' of this extension
    # XXX - should be managed by our caller once these 'protocols' become
    # regular extensions.
    rd_extension_id = 'proto.skype'
    def __init__(self, account, conductor, options):
        self.account = account
        self.doc_model = account.doc_model # this is a little confused...
        self.conductor = conductor
        self.options = options
        self.skype = Skype4Py.Skype()

    def get_rdkey_for_chat(self, chat):
        return ('skype-chat', chat.Name.encode('utf8')) # hrmph!

    def get_rdkey_for_chat_name(self, chat_name): # sob
        return ('skype-chat', chat_name.encode('utf8')) # hrmph!

    def get_rdkey_for_msg(self, msg):
        return ('skype-msg',
                "%s-%d" % (self.account.details['username'], msg._Id))

    def go(self):
        logger.info("attaching to skype...")
        self.account.reportStatus(brat.EVERYTHING, brat.AUTHORIZING)
        self.skype.Attach()

        self.account.reportStatus(brat.EVERYTHING, brat.GOOD)
        logger.info("attached to skype - getting chats")

        self.process_friends((self.skype.CurrentUser,) + self.skype.Friends)

        self.process_chats(self.skype._GetRecentChats(), 'recent', 'recent')
        self.process_chats(self.skype._GetChats(), 'all', 'recent')
        self.process_chats(self.skype._GetChats(), 'all', 'all')

    def process_chats(self, chats, chat_desc, msg_desc):
        keys = [['key-schema_id',
                 [self.get_rdkey_for_chat(c), 'rd.msg.skypechat.raw']]
                for c in chats]
        result = self.doc_model.open_view(keys=keys, reduce=False)
        seen_chats = set([r['value']['rd_key'][1] for r in result['rows']])
        nnew = len(chats)-len(seen_chats)
        logger.debug("skype has %d chat(s) total %d new", len(chats), nnew)
        # fetch recent messages
        logger.info("processing %s chats and %s messages", chat_desc, msg_desc)
        for chat in chats:
            # get the chat properties.
            props = {}
            for p, pt in CHAT_PROPS:
                prop_val = chat._Property(p)
                props['skype_' + p.lower()] = simple_convert(prop_val, pt)
            logger.debug("got chat %r properties: %s", chat.Name, props)
            max_age = self.options.max_age
            if max_age and props['skype_activity_timestamp'] < time.time() - max_age:
                logger.debug("chat is too old - ignoring")
                continue

            # 'Name' is a special case that doesn't come via a prop.  We use
            # 'chatname' as that is the equiv attr on the messages themselves.
            props['skype_chatname'] = chat.Name

            if msg_desc == 'recent':
                self.process_messages(chat._GetRecentMessages(), props, seen_chats, msg_desc)
            elif msg_desc == 'all':
                self.process_messages(chat._GetMessages(), props, seen_chats, msg_desc)
            else:
                raise ValueError, msg_desc
        logger.info("skype has finished processing %s chats", msg_desc)

    def process_messages(self, messages, chat_props, seen_chats, msg_desc):
        logger.debug("chat '%s' has %d %s message(s) total; looking for new ones",
                     chat_props['skype_chatname'], len(messages), msg_desc)

        # Finally got all the messages for this chat.  Execute a view to
        # determine which we have seen (note that we obviously could just
        # fetch the *entire* chats+msgs view once - but we do it this way on
        # purpose to ensure we remain scalable...)
        keys = [['key-schema_id',
                 [self.get_rdkey_for_msg(m), 'rd.msg.skypemsg.raw']]
                 for m in messages]
        result = self.doc_model.open_view(keys=keys, reduce=False)
        msgs_by_id = dict((self.get_rdkey_for_msg(m)[1], m) for m in messages)
        chatname = chat_props['skype_chatname']
        need_chat = chatname not in seen_chats

        seen_msgs = set([r['value']['rd_key'][1] for r in result['rows']])
        remaining = set(msgs_by_id.keys())-set(seen_msgs)
        # we could just process the empty list as normal, but the logging of
        # an info when we do have items is worthwhile...
        if not remaining and not need_chat:
            logger.debug("Chat %r has no new %s items to process", chatname, msg_desc)
            return None
        # we have something to do...
        logger.info("Chat %r has %d %s items to process", chatname,
                    len(remaining), msg_desc)
        logger.debug("we've already seen %d %s items from this chat",
                     len(seen_msgs), msg_desc)

        gen = self.gen_items(chat_props, remaining, msgs_by_id, need_chat)
        self.conductor.pipeline.provide_schema_items([si for si in gen])

    def gen_items(self, chat_props, todo, msgs_by_id, need_chat):
        tow = [] # documents to write.
        if need_chat:
            # we haven't seen the chat itself - do that.
            logger.debug("Creating new skype chat %(skype_chatname)r", chat_props)
            rdkey = self.get_rdkey_for_chat_name(chat_props['skype_chatname'])
            yield {'rd_key' : rdkey,
                   'rd_ext_id': self.rd_extension_id,
                   'rd_schema_id': 'rd.msg.skypechat.raw',
                   'items': chat_props}

        for msgid in todo:
            msg = msgs_by_id[msgid]
            # A new msg in this chat.
            logger.debug("New skype message %d", msg._Id)

            props = {}
            for p, pt in MSG_PROPS:
                prop_val = msg._Property(p)
                props['skype_' + p.lower()] = simple_convert(prop_val, pt)

            props['skype_id'] = msg._Id
            # Denormalize the simple chat properties we need later to avoid us
            # needing to reopen the chat for each message in that chat...
            # XXX - this may bite us later - what happens when the chat subject
            # changes? :(  For now it offers serious speedups, so it goes in.
            props['skype_chat_friendlyname'] = chat_props['skype_friendlyname']
            # and the current members of the chat
            props['skype_chat_members'] = chat_props['skype_members']
    
            # we include the skype username with the ID as they are unique per user.
            rdkey = self.get_rdkey_for_msg(msg)
            yield {'rd_key' : rdkey,
                   'rd_ext_id': self.rd_extension_id,
                   'rd_schema_id': 'rd.msg.skypemsg.raw',
                   'items': props}

        logger.debug("finished processing chat %(skype_chatname)r", chat_props)

    # friends...
    def process_friends(self, friends):
        logger.debug("skype has %d friends(s) total", len(friends))

        schemas = []
        for friend in friends:
            # Simply emit a NULL document with the 'exists' schema - that is
            # just an 'assertion' the identity exists - the framework
            # will take care of handling the fact it may already exist.
            rdkey = ('identity', ('skype', friend._Handle))
            item = rdkey, self.rd_extension_id, 'rd.identity', None, None
            schemas.append({'rd_key' : rdkey,
                            'rd_schema_id' : 'rd.identity.exists',
                            'items' : None,
                            'rd_ext_id': self.rd_extension_id})
        self.conductor.provide_schema_items(schemas)


class SkypeAccount(base.AccountBase):
  def startSync(self, conductor, options):
    return TwistySkype(self, conductor, options).go()

  def get_identities(self):
    return [('skype', self.details['username'])]
