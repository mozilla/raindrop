# The first raindrop unittest!

from twisted.internet import defer

from raindrop.tests import TestCaseWithTestDB, FakeOptions
from raindrop.model import get_doc_model
from raindrop.proto import test as test_proto

import logging
logger = logging.getLogger(__name__)

class TestPipelineBase(TestCaseWithTestDB):
    use_incoming_processor = False
    extensions = None # default extensions for test.
    simple_extensions = [
            'rd.test.core.test_converter',
            'rd.ext.core.msg-rfc-to-email',
            'rd.ext.core.msg-email-to-body',
        ]

    def get_options(self):
        ret = TestCaseWithTestDB.get_options(self)
        ret.exts = self.simple_extensions
        ret.protocols = ['test']
        return ret

    @defer.inlineCallbacks
    def process_doc(self, expected_errors=0):
        # populate our test DB with the raw message(s).
        _ = yield self.deferMakeAnotherTestMessage(None)
        _ = yield self.ensure_pipeline_complete(expected_errors)

    def get_last_by_seq(self, n=1):
        def extract_rows(result):
            ignore_schemas = ['rd.core.workqueue-state', 'rd.core.sync-status']
            rows = result['rows']
            ret = []
            for row in rows:
                if 'doc' in row and 'rd_schema_id' in row['doc']:
                    # Ignore certain other schemas which may get in the way
                    if row['doc']['rd_schema_id'] in ignore_schemas:
                        continue
                    ret.append(row)
                    if len(ret)>=n:
                        break
            assert len(ret)==n # may have too many deleted items and need to re-request?
            return ret

        return get_doc_model().db.listDocsBySeq(limit=n*2,
                                                descending=True,
                                                include_docs=True
                ).addCallback(extract_rows
                )


class TestPipeline(TestPipelineBase):
    extensions = TestPipelineBase.simple_extensions
    def test_one_step(self):
        # Test taking a raw message one step along its pipeline.
        
        test_proto.set_test_options(next_convert_fails=False,
                                    emit_identities=False)

        def check_targets_last(lasts_by_seq, target_types):
            assert len(target_types)==len(lasts_by_seq)
            db_types = set(row['doc']['rd_schema_id'] for row in lasts_by_seq)
            self.failUnlessEqual(db_types, target_types)
            return target_types

        def check_targets(result, target_types):
            # Our targets should be the last written
            return self.get_last_by_seq(len(target_types),
                        ).addCallback(check_targets_last, target_types
                        )

        targets = set(('rd.msg.body', 'rd.msg.email', 'rd.msg.flags', 'rd.tags',
                       'rd.msg.rfc822', 'rd.msg.test.raw'))
        return self.process_doc(
                ).addCallback(check_targets, targets
                )

    def test_one_again_does_nothing(self):
        # Test that attempting to process a message which has already been
        # processed is a noop.
        def check_targets_same(lasts, targets_b4):
            # Re-processing should not have modified the targets in any way.
            db_types = set(row['doc']['rd_schema_id'] for row in lasts)
            self.failUnlessEqual(db_types, targets_b4)

        def check_nothing_done(whateva, targets_b4):
            return self.get_last_by_seq(len(targets_b4),
                        ).addCallback(check_targets_same, targets_b4
                        )

        def reprocess(targets_b4):
            return self.process_doc(
                        ).addCallback(check_nothing_done, targets_b4)

        return self.test_one_step(
                ).addCallback(reprocess
                )

class TestPipelineSync(TestPipeline):
    use_incoming_processor = not TestPipelineBase.use_incoming_processor

class TestErrors(TestPipelineBase):
    extensions = ['rd.test.core.test_converter']

    @defer.inlineCallbacks
    def setUp(self):
        _ = yield super(TestErrors, self).setUp()
        # We expect the following warning records when running this test.
        f = lambda record: "exceptions.RuntimeError: This is a test failure" in record.getMessage()
        self.log_handler.ok_filters.append(f)

    def test_error_stub(self):
        # Test that when a converter fails an appropriate error record is
        # written
        test_proto.set_test_options(next_convert_fails=True)

        def check_target_last(lasts):
            expected = set(('rd.core.error', 'rd.msg.test.raw'))
            types = set([row['doc']['rd_schema_id'] for row in lasts])
            self.failUnlessEqual(types, expected)

        # open the test document to get its ID and _rev, and indicate how many
        # errors we expect.
        return self.process_doc(1
                ).addCallback(lambda whateva: self.get_last_by_seq(2)
                ).addCallback(check_target_last
                )

    def test_reprocess_errors(self):
        # Test that reprocessing an error results in the correct thing.
        def check_target_last(lasts, expected):
            got = set(row['doc']['rd_schema_id'] for row in lasts)
            self.failUnlessEqual(got, expected)

        def start_retry(result):
            test_proto.set_test_options(next_convert_fails=False,
                                        emit_identities=False)
            logger.info('starting retry for %r', result)
            return self.pipeline.start_retry_errors()

        # after the retry we should have the 3 schemas created by our test proto
        expected = set(('rd.msg.flags', 'rd.tags', 'rd.msg.rfc822', 'rd.msg.test.raw'))
        return self.test_error_stub(
                ).addCallback(start_retry
                ).addCallback(lambda whateva: self.get_last_by_seq(len(expected)
                ).addCallback(check_target_last, expected)
                )

    def test_all_steps(self):
        # We test the right thing happens running a 'full' pipeline
        # when our test converter throws an error.
        def check_last_doc(lasts):
            # The tail of the DB should be as below:
            expected = set(['rd.core.error', 'rd.msg.test.raw'])
            # Note the 'rd.core.error' is the failing conversion (ie, the
            # error stub), and no 'later' records exist as they all depend
            # on the failing conversion.
            got = set(l['doc'].get('rd_schema_id') for l in lasts)
            self.failUnlessEqual(got, expected)

        test_proto.set_test_options(next_convert_fails=True)
        return self.process_doc(1
                ).addCallback(lambda whateva: self.get_last_by_seq(2)
                ).addCallback(check_last_doc
                )

class TestErrorsSync(TestErrors):
    use_incoming_processor = not TestPipelineBase.use_incoming_processor
