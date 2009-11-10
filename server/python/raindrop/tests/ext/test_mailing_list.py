from twisted.internet import defer, reactor

from raindrop.tests import TestCaseWithCorpus

# Cases to test:
#
# * a message from a list: the rd.msg.email.mailing-list and rd.mailing-list
#   docs should be created with the appropriate properties;
#
# * a message that updates a list: the list properties should be updated;
#
# The rest of these testcases need to be run separately for Google Groups
# and Mailman lists, since the unsubscription code for those two kinds of lists
# isn't shared between them.  At some point it might be possible to factor out
# some of their code, however, at which point we may be able to merge some of
# the tests.
#
# * a message from a list followed by a newer unsubscribe confirmation:
#   the list state should become "unsubscribed";
#
# * a message from a list followed by an older unsubscribe confirmation:
#   the list state should remain "subscribed";
#
# * an unsubscribe confirmation followed by an older message from the list:
#   the list state should remain "unsubscribed";
#
# * an unsubscribe confirmation followed by a newer message from the list:
#   the list state should become "subscribed";
#
# * an unsubscribe confirmation for a non-existing list: the list doc should
#   be created, and its state should be "unsubscribed" (because we expect to
#   later process some older messages from the list and want to make sure they
#   show up as being from an unsubscribed list);
#
# * an unsubscribe confirmation for a non-existing list followed by an older
#   message from the list: the list state should remain "unsubscribed";
#
# * an unsubscribe confirmation for a non-existing list followed by a newer
#   message from the list: the list state should become "subscribed";
#
# * an unsubscribe confirm request for a list in the "unsubscribe-pending"
#   state: an rd.msg.outgoing.simple doc should be created with the appropriate
#   properties; the list state should become "unsubscribe-confirmed";

class TestSimpleCorpus(TestCaseWithCorpus):
    def ensure_doc(self, doc, expected_doc):
        # Generate a list of the properties of the document.
        # We ignore private properties of CouchDB (start with underscore)
        # and Raindrop (start with "rd_"), as we are only testing the public
        # properties generated by our extension.
        actual_properties = sorted([key for key in doc.keys()
                                        if not key.startswith('_')
                                        and not key.startswith('rd_')])

        expected_properties = sorted([key for key in expected_doc.keys()])

        # The document should have the expected properties.
        self.failUnlessEqual(actual_properties, expected_properties,
                             repr(doc['rd_key']) + ' properties')

        # The document's properties should have the expected values.
        for property in expected_doc:
            self.failUnlessEqual(doc[property], expected_doc[property],
                                 repr(doc['rd_key']) + '::' + property)

    @defer.inlineCallbacks
    def get_docs(self, key, expected=None):
        result = yield self.doc_model.open_view(key=key, reduce=False,
                                                include_docs=True)
        rows = result['rows']
        if expected is not None:
            self.failUnlessEqual(len(rows), expected,
                                 'num rows for key ' + repr(key))
        docs = [row['doc'] for row in rows]
        defer.returnValue(docs)

    @defer.inlineCallbacks
    def put_docs(self, corpus_name, corpus_spec="*", expected=None):
        items = [d for d in self.gen_corpus_schema_items(corpus_name, corpus_spec)]
        if expected is not None:
            self.failUnlessEqual(len(items), expected)
        _ = yield self.doc_model.create_schema_items(items)
        _ = yield self.ensure_pipeline_complete()

    @defer.inlineCallbacks
    def test_mailing_list(self):
        # Initialize the corpus & database.
        yield self.init_corpus('mailing-list')

        # Process one message from a mailing list.
        yield self.put_docs('mailing-list', 'simple-message', 1)

        mail_key = ['rd.core.content', 'schema_id', 'rd.msg.email.mailing-list']
        list_key = ['rd.core.content', 'schema_id', 'rd.mailing-list']

        # There should be one rd.msg.email.mailing-list document.
        doc = (yield self.get_docs(mail_key, expected=1))[0]

        # The document should have the expected properties/values.
        expected_doc = {
            'list_id': 'test.lists.example.com'
        }
        self.ensure_doc(doc, expected_doc)

        # There should be one rd.mailing-list document.
        doc = (yield self.get_docs(list_key, expected=1))[0]

        # The document should have the expected properties/values.
        expected_doc = {
            'changed_timestamp': 1251344732,
            'help': 'mailto:test-request@lists.example.com?subject=help',
            'id': 'test.lists.example.com',
            'identity': ['email', 'raindrop_test_user@mozillamessaging.com'],
            'name': 'test list',
            'post': 'mailto:test@lists.example.com',
            'status': 'subscribed',
            'subscribe': 'https://lists.example.com/listinfo/test>,\n\t' +
                '<mailto:test-request@lists.example.com?subject=subscribe',
            'unsubscribe': 'https://lists.example.com/options/test>,\n\t' +
                '<mailto:test-request@lists.example.com?subject=unsubscribe',
        }
        self.ensure_doc(doc, expected_doc)

        # Process a second, later message from the same mailing list.
        yield self.put_docs('mailing-list', 'simple-message-2', 1)

        # There should now be two rd.msg.email.mailing-list documents.
        yield self.get_docs(mail_key, expected=2)

        # There should only be one rd.msg.email.mailing-list document
        # with the key of the message we just processed.
        message_key = ['rd.core.content', 'key-schema_id',
                [['email', '40c05b9d93ba4695a30e72174c5c8126@example.com'],
                'rd.msg.email.mailing-list']]
        doc = (yield self.get_docs(message_key, expected=1))[0]

        # The document should have the expected properties/values.
        expected_doc = {
            'list_id': 'test.lists.example.com'
        }
        self.ensure_doc(doc, expected_doc)

        # There should still be just one rd.mailing-list document.
        doc = (yield self.get_docs(list_key, expected=1))[0]

        # The document should have the expected properties/values.
        #
        # Some of these properties (subscribe, unsubscribe) have changed
        # in the new message and should have been updated in the doc;
        # another (post) hasn't changed and should have the same value;
        # one (help) wasn't provided by the second message at all, so we leave
        # its original value in place (some lists don't provide all List-*
        # headers when they send admin messages, but that shouldn't cause us
        # to remove their properties); and one (archive) is new and should have
        # been added to the doc.
        #
        # Finally, since the list doc has been changed, its changed timestamp
        # should have been updated to the date of the second message.
        #
        expected_doc = {
            'archive': 'https://lists.example.com/archive/thetest',
            'changed_timestamp': 1251401696,
            'help': 'mailto:test-request@lists.example.com?subject=help',
            'id': 'test.lists.example.com',
            'identity': ['email', 'raindrop_test_user@mozillamessaging.com'],
            'name': 'the test list',
            'post': 'mailto:test@lists.example.com',
            'status': 'subscribed',
            'subscribe': 'https://lists.example.com/listinfo/thetest>,\n\t' +
                '<mailto:thetest-request@lists.example.com?subject=subscribe',
            'unsubscribe': 'https://lists.example.com/options/thetest>,\n\t' +
                '<mailto:thetest-request@lists.example.com?subject=unsubscribe',
        }
        self.ensure_doc(doc, expected_doc)

    @defer.inlineCallbacks
    def test_mailman_message_older_unsub_conf_newer(self):
        # Initialize the corpus & database.
        yield self.init_corpus('mailing-list')

        # Process an older message then a newer unsubscribe confirmation.
        yield self.put_docs('mailing-list', 'mailman-message-older', 1)
        yield self.put_docs('mailing-list', 'mailman-unsub-conf-newer', 1)

        # The list status should be "unsubscribed".
        list_key = ['rd.core.content', 'schema_id', 'rd.mailing-list']
        doc = (yield self.get_docs(list_key))[0]
        self.failUnlessEqual(doc['status'], 'unsubscribed',
                             repr('Mailman list status is unsubscribed'))

    @defer.inlineCallbacks
    def test_mailman_message_newer_unsub_conf_older(self):
        # Initialize the corpus & database.
        yield self.init_corpus('mailing-list')

        # Process an older message then a newer unsubscribe confirmation.
        yield self.put_docs('mailing-list', 'mailman-message-newer', 1)
        yield self.put_docs('mailing-list', 'mailman-unsub-conf-older', 1)

        # The list status should be "subscribed".
        list_key = ['rd.core.content', 'schema_id', 'rd.mailing-list']
        doc = (yield self.get_docs(list_key))[0]
        self.failUnlessEqual(doc['status'], 'subscribed',
                             repr('Mailman list status is subscribed'))

    @defer.inlineCallbacks
    def test_mailman_unsub_conf_newer_message_older(self):
        # Initialize the corpus & database.
        yield self.init_corpus('mailing-list')

        # Process a newer unsubscribe confirmation then an older message.
        yield self.put_docs('mailing-list', 'mailman-unsub-conf-newer', 1)
        yield self.put_docs('mailing-list', 'mailman-message-older', 1)

        # The list status should be "unsubscribed".
        list_key = ['rd.core.content', 'schema_id', 'rd.mailing-list']
        doc = (yield self.get_docs(list_key))[0]
        self.failUnlessEqual(doc['status'], 'unsubscribed',
                             repr('Mailman list status is unsubscribed'))

    @defer.inlineCallbacks
    def test_mailman_unsub_conf_older_message_newer(self):
        # Initialize the corpus & database.
        yield self.init_corpus('mailing-list')

        # Process an older unsubscribe confirmation then a newer message.
        yield self.put_docs('mailing-list', 'mailman-unsub-conf-older', 1)
        yield self.put_docs('mailing-list', 'mailman-message-newer', 1)

        # The list status should be "subscribed".
        list_key = ['rd.core.content', 'schema_id', 'rd.mailing-list']
        doc = (yield self.get_docs(list_key))[0]
        self.failUnlessEqual(doc['status'], 'subscribed',
                             repr('Mailman list status is subscribed'))

    # Just an unsubscribe confirmation from a Mailman list.  The list should be
    # created, and its status should be "unsubscribed".
    @defer.inlineCallbacks
    def test_mailman_unsub_conf(self):
        # Initialize the corpus & database.
        yield self.init_corpus('mailing-list')

        # Process an unsubscribe confirmation.
        yield self.put_docs('mailing-list', 'mailman-unsub-conf-newer', 1)

        list_key = ['rd.core.content', 'schema_id', 'rd.mailing-list']
        doc = (yield self.get_docs(list_key, expected=1))[0]
        self.failUnlessEqual(doc['status'], 'unsubscribed',
                             repr('Mailman list status is unsubscribed'))

    # An unsubscription notification (not confirmation) from a Mailman list
    # to which the user is subscribed and which the user owns, informing
    # the user that someone *else* has been unsubscribed.  The list status
    # should remain "subscribed" in this case, since it isn't the user
    # who was unsubscribed.
    @defer.inlineCallbacks
    def test_mailman_unsub_note(self):
        # Initialize the corpus & database.
        yield self.init_corpus('mailing-list')

        # Process a message to create the mailing list record.
        yield self.put_docs('mailing-list', 'mailman-message-older', 1)

        # Process the unsubscribe notification.
        yield self.put_docs('mailing-list', 'mailman-unsub-note', 1)

        list_key = ['rd.core.content', 'schema_id', 'rd.mailing-list']
        doc = (yield self.get_docs(list_key, expected=1))[0]
        self.failUnlessEqual(doc['status'], 'subscribed',
                             repr('Mailman list status is subscribed'))

    # The mailing list is in the "unsubscribe-pending" state, and Raindrop
    # receives a request to confirm unsubscription.  Raindrop should create
    # a message confirming the unsubscription and set the list state to
    # "unsubscribe-confirmed".
    @defer.inlineCallbacks
    def test_mailman_unsub_conf_req(self):
        # Initialize the corpus & database.
        yield self.init_corpus('mailing-list')

        # Process a message to create the mailing list record.
        yield self.put_docs('mailing-list', 'mailman-message-older', 1)

        # Put the mailing list record into the "unsubscribe-pending" state.
        list_key = ['rd.core.content', 'schema_id', 'rd.mailing-list']
        doc = (yield self.get_docs(list_key))[0]
        doc['status'] = "unsubscribe-pending"
        _ = yield self.doc_model.db.updateDocuments([doc])

        # Process the confirm unsubscribe request.
        yield self.put_docs('mailing-list', 'mailman-unsub-conf-req', 1)

        # There should be an outgoing message confirming the unsubscription.
        message_key = ['rd.core.content', 'schema_id', 'rd.msg.outgoing.simple']
        doc = (yield self.get_docs(message_key, expected=1))[0]

        # The outgoing message should have the expected properties/values.
        expected_doc = {
            'body': '',
            'from': ['email', 'raindrop_test_user@mozillamessaging.com'],
            'from_display': 'raindrop_test_user@mozillamessaging.com',
            'outgoing_state': 'outgoing',
            'subject': 'Your confirmation is required to leave the test mailing list',
            'to': [['email',
                    'test-confirm+018e404890076d94e6026d8333c887f8edd0c41f@lists.example.com']],
            'to_display': ['']
        }
        self.ensure_doc(doc, expected_doc)

        # The status of the mailing list should be "unsubscribe-confirmed".
        doc = (yield self.get_docs(list_key))[0]
        self.failUnlessEqual(doc['status'], 'unsubscribe-confirmed',
                             repr('list status is unsubscribe-confirmed'))

    @defer.inlineCallbacks
    def test_google_groups_message_older_unsub_conf_newer(self):
        # Initialize the corpus & database.
        yield self.init_corpus('mailing-list')

        # Process an older message then a newer unsubscribe confirmation.
        yield self.put_docs('mailing-list', 'google-groups-message-older', 1)
        yield self.put_docs('mailing-list', 'google-groups-unsub-conf-newer', 1)

        # The list status should be "unsubscribed".
        list_key = ['rd.core.content', 'schema_id', 'rd.mailing-list']
        doc = (yield self.get_docs(list_key))[0]
        self.failUnlessEqual(doc['status'], 'unsubscribed',
                             repr('Google Groups list status is unsubscribed'))

    @defer.inlineCallbacks
    def test_google_groups_message_newer_unsub_conf_older(self):
        # Initialize the corpus & database.
        yield self.init_corpus('mailing-list')

        # Process an older message then a newer unsubscribe confirmation.
        yield self.put_docs('mailing-list', 'google-groups-message-newer', 1)
        yield self.put_docs('mailing-list', 'google-groups-unsub-conf-older', 1)

        # The list status should be "subscribed".
        list_key = ['rd.core.content', 'schema_id', 'rd.mailing-list']
        doc = (yield self.get_docs(list_key))[0]
        self.failUnlessEqual(doc['status'], 'subscribed',
                             repr('Google Groups list status is subscribed'))

    @defer.inlineCallbacks
    def test_google_groups_unsub_conf_newer_message_older(self):
        # Initialize the corpus & database.
        yield self.init_corpus('mailing-list')

        # Process a newer unsubscribe confirmation then an older message.
        yield self.put_docs('mailing-list', 'google-groups-unsub-conf-newer', 1)
        yield self.put_docs('mailing-list', 'google-groups-message-older', 1)

        # The list status should be "unsubscribed".
        list_key = ['rd.core.content', 'schema_id', 'rd.mailing-list']
        doc = (yield self.get_docs(list_key))[0]
        self.failUnlessEqual(doc['status'], 'unsubscribed',
                             repr('Google Groups list status is unsubscribed'))

    @defer.inlineCallbacks
    def test_google_groups_unsub_conf_older_message_newer(self):
        # Initialize the corpus & database.
        yield self.init_corpus('mailing-list')

        # Process an older unsubscribe confirmation then a newer message.
        yield self.put_docs('mailing-list', 'google-groups-unsub-conf-older', 1)
        yield self.put_docs('mailing-list', 'google-groups-message-newer', 1)

        # The list status should be "subscribed".
        list_key = ['rd.core.content', 'schema_id', 'rd.mailing-list']
        doc = (yield self.get_docs(list_key))[0]
        self.failUnlessEqual(doc['status'], 'subscribed',
                             repr('Google Groups list status is subscribed'))

    # Just an unsubscribe confirmation from a Google Groups list.  The list
    # should be created, and its status should be set to "unsubscribed".
    @defer.inlineCallbacks
    def test_google_groups_unsub_conf(self):
        # Initialize the corpus & database.
        yield self.init_corpus('mailing-list')

        # Process an unsubscribe confirmation.
        yield self.put_docs('mailing-list', 'google-groups-unsub-conf-newer', 1)

        list_key = ['rd.core.content', 'schema_id', 'rd.mailing-list']
        yield self.get_docs(list_key, expected=1)

    # The mailing list is in the "unsubscribe-pending" state, and Raindrop
    # receives a request to confirm unsubscription.  Raindrop should create
    # a message confirming the unsubscription and set the list state to
    # "unsubscribe-confirmed".
    @defer.inlineCallbacks
    def test_google_groups_unsub_conf_req(self):
        # Initialize the corpus & database.
        yield self.init_corpus('mailing-list')

        # Process a message to create the mailing list record.
        yield self.put_docs('mailing-list', 'google-groups-message-older', 1)

        # Put the mailing list record into the "unsubscribe-pending" state.
        list_key = ['rd.core.content', 'schema_id', 'rd.mailing-list']
        doc = (yield self.get_docs(list_key))[0]
        doc['status'] = "unsubscribe-pending"
        _ = yield self.doc_model.db.updateDocuments([doc])

        # Process the confirm unsubscribe request.
        yield self.put_docs('mailing-list', 'google-groups-unsub-conf-req', 1)

        # There should be an outgoing message confirming the unsubscription.
        message_key = ['rd.core.content', 'schema_id', 'rd.msg.outgoing.simple']
        doc = (yield self.get_docs(message_key, expected=1))[0]

        # The outgoing message should have the expected properties/values.
        expected_doc = {
            'body': '',
            'from': ['email', 'raindrop_test_user@mozillamessaging.com'],
            'from_display': 'raindrop_test_user@mozillamessaging.com',
            'outgoing_state': 'outgoing',
            'subject': '',
            'to': [['email',
                    'mozilla-labs-personas+unsubconfirm-sdcHiwwAAAAL-sWq-5e5BoW-SoL8Od9V@googlegroups.com']],
            'to_display': ['']
        }
        self.ensure_doc(doc, expected_doc)

        # The status of the mailing list should be "unsubscribe-confirmed".
        doc = (yield self.get_docs(list_key))[0]
        self.failUnlessEqual(doc['status'], 'unsubscribe-confirmed',
                             repr('list status is unsubscribe-confirmed'))

