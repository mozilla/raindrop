from twisted.internet import defer

from raindrop.tests import TestCaseWithCorpus


class TestSimpleCorpus(TestCaseWithCorpus):
    @defer.inlineCallbacks
    def test_simple_notification(self):
        ndocs = yield self.load_corpus("hand-rolled", "sent-email-simple-reply")
        _ = yield self.ensure_pipeline_complete()


        # open all grouping-tag schemas - should be only 1
        key = ["rd.core.content", "schema_id", "rd.msg.grouping-tag"]
        result = yield self.doc_model.open_view(key=key, reduce=False,
                                                include_docs=True)
        # Check that grouping-tag is 'direct'.
        rows = result['rows']
        self.failUnlessEqual(len(rows), 1)
        self.failUnlessEqual(rows[0]['doc']['tag'], 'direct')

    @defer.inlineCallbacks
    def test_bulk_sender(self):
        # first run the extension.
        _ = yield self.test_simple_notification()

        # now create a schema item indicating this sender is a 'bulk sender'
        rdkey = ['identity', ['email', 'raindrop_test_recip@mozillamessaging.com']]
        si = {
            'rd_key': rdkey,
            'rd_schema_id': 'rd.identity.msg-grouping-tag',
            'rd_ext_id': 'rd.testsuite',
            'items' : {
                'tag': 'broadcast',
            }
        }
        _ = yield self.doc_model.create_schema_items([si])
        _ = yield self.ensure_pipeline_complete()

        # open all grouping-tag schemas - should be only 1
        key = ["rd.core.content", "schema_id", "rd.msg.grouping-tag"]
        result = yield self.doc_model.open_view(key=key, reduce=False,
                                                include_docs=True)
        # Check the grouping-tag schema for the identity caused the message
        # to be reported as 'broadcast'.
        rows = result['rows']
        self.failUnlessEqual(len(rows), 1)
        self.failUnlessEqual(rows[0]['doc']['tag'], 'broadcast')


class TestSimpleCorpusBacklog(TestSimpleCorpus):
    use_incoming_processor = not TestSimpleCorpus.use_incoming_processor
