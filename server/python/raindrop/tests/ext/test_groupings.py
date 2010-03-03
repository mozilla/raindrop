from pprint import pformat
from twisted.internet import defer, reactor

from raindrop.tests import TestCaseWithCorpus, TestCaseWithTestDB


class TestSimpleCorpus(TestCaseWithCorpus):
    @defer.inlineCallbacks
    def put_docs(self, corpus_name, corpus_spec="*", expected=None):
        items = [d for d in self.gen_corpus_schema_items(corpus_name, corpus_spec)]
        if expected is not None:
            self.failUnlessEqual(len(items), expected)
        _ = yield self.doc_model.create_schema_items(items)
        _ = yield self.ensure_pipeline_complete()

    @defer.inlineCallbacks
    def test_simple_notification(self):
        ndocs = yield self.load_corpus("hand-rolled", "sent-email-simple-reply")
        _ = yield self.ensure_pipeline_complete()

        # open all grouping-tag schemas - should be only 1
        key = ["rd.core.content", "schema_id", "rd.msg.grouping-tag"]
        result = yield self.doc_model.open_view(key=key, reduce=False,
                                                include_docs=True)
        # Check that grouping-tag specifies a tag for us
        rows = result['rows']
        self.failUnlessEqual(len(rows), 1)
        ex_tag = 'identity-email-raindrop_test_user@mozillamessaging.com'
        self.failUnlessEqual(rows[0]['doc']['tag'], ex_tag)
        # The back-end boostrap process has arranged for "our identities" to
        # be associated with the inflow grouping.
        ex_grouping_key = ['display-group', 'inflow']
        key = ["rd.core.content", "schema_id", "rd.grouping.summary"]
        result = yield self.doc_model.open_view(key=key, reduce=False,
                                                include_docs=True)
        rows = result['rows']
        self.failUnlessEqual(len(rows), 1)
        self.failUnlessEqual(rows[0]['doc']['rd_key'], ex_grouping_key)

    @defer.inlineCallbacks
    def test_bulk_sender(self):
        # first run the extension.
        _ = yield self.test_simple_notification()

        # now create a schema item indicating this sender is a 'bulk sender'
        rdkey = ['identity', ['email', 'raindrop_test_recip@mozillamessaging.com']]
        si = {
            'rd_key': rdkey,
            'rd_schema_id': 'rd.identity.sender-flags',
            'rd_ext_id': 'rd.testsuite',
            'items' : {
                'bulk': 'true',
            }
        }
        _ = yield self.doc_model.create_schema_items([si])
        _ = yield self.ensure_pipeline_complete()

        # open all grouping-tag schemas - should be only 1
        key = ["rd.core.content", "schema_id", "rd.msg.grouping-tag"]
        result = yield self.doc_model.open_view(key=key, reduce=False,
                                                include_docs=True)
        # Check the grouping-tag schema for the identity caused the message
        # to be reported as the *senders* tag
        rows = result['rows']
        self.failUnlessEqual(len(rows), 1)
        self.failUnlessEqual(rows[0]['doc']['tag'],
                             'identity-email-raindrop_test_recip@mozillamessaging.com')
        # And that we have a grouping-summary for this sender (ie, it is no
        # longer in the 'inflow' group.)
        ex_grouping_key = ['identity', ['email', 'raindrop_test_recip@mozillamessaging.com']]
        key = ["rd.core.content", "schema_id", "rd.grouping.summary"]
        result = yield self.doc_model.open_view(key=key, reduce=False,
                                                include_docs=True)
        rows = result['rows']
        self.failUnlessEqual(len(rows), 1)
        self.failUnlessEqual(rows[0]['doc']['rd_key'], ex_grouping_key)

    @defer.inlineCallbacks
    def test_groups_single(self):
        # Initialize the corpus & database.
        ndocs = yield self.load_corpus("hand-rolled", "sent-email-simple-reply")
       #self.failUnlessEqual(ndocs, 1)
        _ = yield self.ensure_pipeline_complete()

        msgid = ['email', '78cb2eb5dbc74cdd9691dcfdb266d1b9@something']
        body_schema = (yield self.doc_model.open_schemas([(msgid, 'rd.msg.body')]))[0]
        # should be one 'rd.convo.summary' doc in the DB.
        key = ['rd.core.content', 'schema_id', 'rd.conv.summary']
        result = yield self.doc_model.open_view(key=key, reduce=False,
                                                include_docs=True)
        rows = result['rows']
        self.failUnlessEqual(len(rows), 1, pformat(rows))
        self.failUnlessEqual(rows[0]['doc']['rd_schema_id'], 'rd.conv.summary')
        conv_id = rows[0]['doc']['rd_key']

        # should also be exactly 1 'grouping summary'
        key = ['rd.core.content', 'schema_id', 'rd.grouping.summary']
        result = yield self.doc_model.open_view(key=key, reduce=False,
                                                include_docs=True)
        rows = result['rows']
        self.failUnlessEqual(len(rows), 1, pformat(rows))
        self.failUnlessEqual(rows[0]['doc']['rd_schema_id'], 'rd.grouping.summary')
        doc_sum = rows[0]['doc']
        expected_doc = {
            'unread' : [conv_id],
            'num_unread': 1,
        }
        self.failUnlessDocEqual(doc_sum, expected_doc)


class TestSimpleCorpusBacklog(TestSimpleCorpus):
    use_incoming_processor = not TestSimpleCorpus.use_incoming_processor


class TestCustom(TestCaseWithTestDB):
    msg_template = """\
Delivered-To: raindrop_test_user@mozillamessaging.com
From: %s
To: %s
Date: Sat, 21 Jul 2009 12:13:14 -0000
Message-Id: <1234@something>

Hello everyone
"""
    my_addy = 'raindrop_test_user@mozillamessaging.com'
    other_addy = 'someone@somewhere.com'
    bulk_addy = 'newsletter@somewhere.com'


    def make_config(self):
        config = TestCaseWithTestDB.make_config(self)
        # now clobber it with a fake imap account which has our test user.
        config.accounts = {}
        acct = config.accounts['test'] = {}
        acct['proto'] = 'imap'
        acct['id'] = 'imap_test'
        acct['username'] = self.my_addy
        return config

    @defer.inlineCallbacks
    def check_grouping(self, from_addy, to_addys, expected_addy, bulk_flag):
        to_str = ",".join(to_addys)
        msg = self.msg_template % (from_addy, to_str)
        si = {'rd_key': ['email', '1234@something'],
              'rd_schema_id': 'rd.msg.rfc822',
              'rd_source' : None,
              'rd_ext_id': 'rd.testsuite',
              'items': {},
              'attachments' : {
                    'rfc822': {
                        'data': msg,
                    }
              }
        }
        _ = yield self.doc_model.create_schema_items([si])
        if bulk_flag:
            si = {
                'rd_key': ['identity', ['email', self.bulk_addy]],
                'rd_schema_id': 'rd.identity.sender-flags',
                'rd_ext_id': 'rd.testsuite',
                'items' : {
                    'bulk': 'true',
                }
            }
            _ = yield self.doc_model.create_schema_items([si])
        _ = yield self.ensure_pipeline_complete()
        # should also be exactly 1 'grouping-tag' schema for the message.
        rd_key = ['email', '1234@something']
        docs = yield self.doc_model.open_schemas([(rd_key, 'rd.msg.grouping-tag')])
        doc = docs[0]
        self.failUnless(doc, 'no grouping-tag schema')
        ex = 'identity-email-' + expected_addy
        self.failUnlessEqual(doc['tag'], ex)

    # This table from msg-email-to-grouping-tag
    #Scenario                       no bulk flag          bulk flag
    #-----------------------        --------              ----------

    #From: you; to: bulk            you/inflow            bulk 
    def test_you_bulk(self):
        return self.check_grouping(self.my_addy, [self.bulk_addy],
                                   self.my_addy, False)

    def test_you_bulk_flagged(self):
        return self.check_grouping(self.my_addy, [self.bulk_addy],
                                   self.bulk_addy, True)
    
    #From: you; to: other           you/inflow            you/inflow
    def test_you_other(self):
        return self.check_grouping(self.my_addy, [self.other_addy],
                                   self.my_addy, False)

    def test_you_other_flagged(self):
        return self.check_grouping(self.my_addy, [self.other_addy],
                                   self.my_addy, True)

    #From: other; to: bulk          bulk* or other        bulk
    def test_other_bulk(self):
        return self.check_grouping(self.other_addy, [self.bulk_addy],
                                   self.bulk_addy, False)

    def test_other_bulk_flagged(self):
        return self.check_grouping(self.other_addy, [self.bulk_addy],
                                   self.bulk_addy, True)

    #From: other; to: you           you/inflow            you/inflow
    def test_other_you(self):
        return self.check_grouping(self.other_addy, [self.my_addy],
                                   self.my_addy, False)

    def test_other_you_flagged(self):
        return self.check_grouping(self.other_addy, [self.my_addy],
                                   self.my_addy, True)

    #From: other; to: bulk, you     you/inflow            you/inflow
    def test_other_bulk_you(self):
        return self.check_grouping(self.other_addy,
                                   [self.my_addy, self.bulk_addy],
                                   self.my_addy, False)

    def test_other_bulk_you_flagged(self):
        return self.check_grouping(self.other_addy,
                                   [self.my_addy, self.bulk_addy],
                                   self.my_addy, True)

    #From: bulk ; to: other         bulk or *other         bulk 
    def test_bulk_other(self):
        return self.check_grouping(self.bulk_addy, [self.other_addy],
                                   self.other_addy, False)

    def test_bulk_other_flagged(self):
        return self.check_grouping(self.bulk_addy, [self.other_addy],
                                   self.bulk_addy, True)

    #From: bulk ; to: you           you                   bulk 
    def test_bulk_you(self):
        return self.check_grouping(self.bulk_addy, [self.my_addy],
                                   self.my_addy, False)

    def test_bulk_you_flagged(self):
        return self.check_grouping(self.bulk_addy, [self.my_addy],
                                   self.bulk_addy, True)

    #From: bulk ; to: other, you    you/inflow            bulk
    def test_bulk_other_you(self):
        return self.check_grouping(self.bulk_addy,
                                   [self.other_addy, self.my_addy],
                                   self.my_addy, False)

    def test_bulk_other_you_flagged(self):
        return self.check_grouping(self.bulk_addy,
                                   [self.other_addy, self.my_addy],
                                   self.bulk_addy, True)

    #From: other; to: <none>        other                 other
    def test_other_none(self):
        return self.check_grouping(self.other_addy, [], self.other_addy, False)

    def test_other_none_flagged(self):
        return self.check_grouping(self.other_addy, [], self.other_addy, True)

    #From: bulk ; to: <none>        bulk                  bulk
    def test_bulk_none(self):
        return self.check_grouping(self.bulk_addy, [], self.bulk_addy, False)

    def test_bulk_none_flagged(self):
        return self.check_grouping(self.bulk_addy, [], self.bulk_addy, True)


class TestCustomBacklog(TestCustom):
    use_incoming_processor = not TestSimpleCorpus.use_incoming_processor