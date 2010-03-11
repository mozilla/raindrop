from pprint import pformat

from twisted.internet import defer
from raindrop.tests.api import APITestCase

class TestConvoSimple(APITestCase):
    # "my identity" in the context of these tests should give us
    # 'raindrop_test_user@mozillamessaging.com' - and we know he
    # participates in at least the following convos.
    def get_known_msgs_from_identities(self, iids=None):
        known_msgs = set()
        if iids is None or \
           ('email', 'raindrop_test_user@mozillamessaging.com') in iids:
            # sent-email-simple.rfc822.txt
            known_msgs.add(('email', 'd3d08a8a534c464881a95b75300e9011@something'))
        # a skype convo
        if iids is None or ('skype', 'raindrop_test_user') in iids:
            # Our test user also has a skype identity.
            known_msgs.add(('skype-msg', 'raindrop_test_user-1'))
        return known_msgs

    def get_known_msgs_to_identities(self, iids=None):
        known_msgs = set()
        if iids is None or \
           ('email', 'raindrop_test_user@mozillamessaging.com') in iids:
            # sent-email-simple-reply.rfc822.txt
            known_msgs.add(('email', '78cb2eb5dbc74cdd9691dcfdb266d1b9@something'))
        return known_msgs

    def get_known_msgs_not_from_identities(self):
        # some 'random' messages not associated with our test identities.
        return set([('email', '07316ced2329a69aa169f3b9c6467703@bitbucket.org')])

    @defer.inlineCallbacks
    def sanity_check_convo(self, convo):
        # all messages in a convo must have the same conversation ID.
        messages = convo['messages']
        keys = [['rd.core.content', 'key-schema_id', [msg['id'], 'rd.msg.conversation']]
                for msg in messages]
        result = yield self.doc_model.open_view(keys=keys, reduce=False,
                                                include_docs=True)
        for row in result['rows']:
            exp = convo['id']
            got = row['doc']['conversation_id']
            self.failUnlessEqual(exp, got,
                                 "wanted %r - got %r: %s" % (exp, got, pformat(row)))

        # No message should appear twice.
        seen_keys = set([tuple(msg['id']) for msg in messages])
        self.failUnlessEqual(len(seen_keys), len(messages), str(seen_keys))

    @defer.inlineCallbacks
    def test_identities_mine(self, iids=None):
        known_msgs = self.get_known_msgs_to_identities(iids)
        known_msgs.update(self.get_known_msgs_from_identities(iids))
        result = yield self.call_api("inflow/conversations/identities", ids=iids)
        seen = set()
        for convo in result:
            _ = yield self.sanity_check_convo(convo)
            for msg in convo['messages']:
                seen.add(tuple(msg['id']))

        self.failUnlessEqual(seen.intersection(known_msgs), known_msgs)
        unknown_msgs = self.get_known_msgs_not_from_identities()
        self.failUnlessEqual(seen.intersection(unknown_msgs), set())

    def test_identities_specific(self):
        # check it works when our default user is explicitly specified.
        iids = [('email', 'raindrop_test_user@mozillamessaging.com')]
        return self.test_identities_mine(iids)

    @defer.inlineCallbacks
    def test_direct(self, endpoint="inflow/conversations/direct",
                    schemas=None):
        known_msgs = self.get_known_msgs_to_identities()
        result = yield self.call_api(endpoint, schemas=schemas)
        seen = set()
        for convo in result:
            _ = yield self.sanity_check_convo(convo)
            for msg in convo['messages']:
                seen.add(tuple(msg['id']))
                # check the 'rd_*' fields have been removed.
                for schid, schvals in msg['schemas'].iteritems():
                    self.failIf('rd_key' in schvals, schvals)

                # at the moment we *always* return the summary schemas; we
                # know rd.msg.body is one of these.
                self.failUnless('rd.msg.body' in msg['schemas'], pformat(msg['schemas']))
                if schemas is not None:
                    if schemas != ['*']:
                        for schema in schemas:
                            self.failUnless(schema in msg['schemas'], pformat(msg['schemas']))
                    if schemas == ['*'] or 'rd.msg.body' in schemas:
                        # Here we test that the *full* body schema was returned,
                        # not just the summary one. The 'body' field is not in the
                        # summary, so check it was actually returned.
                        body = msg['schemas']['rd.msg.body']
                        self.failUnless('body' in body, pformat(body))

        self.failUnlessEqual(seen.intersection(known_msgs), known_msgs)
        unknown_msgs = self.get_known_msgs_not_from_identities()
        self.failUnlessEqual(seen.intersection(unknown_msgs), set())

    @defer.inlineCallbacks
    def test_personal(self):
        _ = yield self.test_direct("inflow/conversations/personal")

    @defer.inlineCallbacks
    def test_personal_star(self):
        _ = yield self.test_direct("inflow/conversations/personal", ['*'])

    @defer.inlineCallbacks
    def test_personal_specific_conv(self):
        _ = yield self.test_direct("inflow/conversations/personal",
                                   ['rd.msg.conversation'])

    def test_personal_specific_body(self):
        _ = yield self.test_direct("inflow/conversations/personal",
                                   ['rd.msg.body'])

    @defer.inlineCallbacks
    def test_twitter(self):
        result = yield self.call_api("inflow/conversations/twitter")
        # confirm 3 conversations
        self.failUnlessEqual(3, len(result), pformat(result))

        # get the conversations and sanity check them.
        ex_ids = [['tweet', tid] for tid in [6119612045, 11111, 22222]]
        seen_ids = []
        for convo in result:
            _ = yield self.sanity_check_convo(convo)

            # confirm only one message
            self.failUnlessEqual(1, len(convo['messages']), pformat(convo))

            msg = convo['messages'][0]
            # record the message ID
            seen_ids.append(msg['id'])

        self.failUnlessEqual(sorted(seen_ids), sorted(ex_ids))

    @defer.inlineCallbacks
    def test_with_messages(self):
        known_msgs = self.get_known_msgs_to_identities()
        result = yield self.call_api("inflow/conversations/with_messages",
                                     keys=list(known_msgs))
        # should be 1 convo
        self.failUnlessEqual(len(result), 1)
        _ = yield self.sanity_check_convo(result[0])
        seen=set()
        for msg in result[0]['messages']:
            seen.add(self.doc_model.hashable_key(msg['id']))
            # check the 'rd_*' fields have been removed.
            for schid, schvals in msg['schemas'].iteritems():
                self.failIf('rd_key' in schvals, schvals)
        self.failUnlessEqual(known_msgs.intersection(seen), known_msgs)

    @defer.inlineCallbacks
    def test_by_id(self):
        known_msgs = self.get_known_msgs_to_identities()
        # find the conv IDs
        keys = [['rd.core.content', 'key-schema_id', [mid, 'rd.msg.conversation']]
                for mid in known_msgs]
        result = yield self.doc_model.open_view(keys=keys, reduce=False,
                                                include_docs=True)
        # should be 1 convo
        self.failUnlessEqual(len(result['rows']), len(keys))
        conv_id = None
        for row in result['rows']:
            if conv_id is None:
                conv_id = row['doc']['conversation_id']
            else:
                self.failUnlessEqual(conv_id, row['doc']['conversation_id'])

        result = yield self.call_api("inflow/conversations/by_id",
                                     key=conv_id)
        _ = yield self.sanity_check_convo(result)
        seen = set()
        for msg in result['messages']:
            seen.add(self.doc_model.hashable_key(msg['id']))
        self.failUnlessEqual(known_msgs.intersection(seen), known_msgs)
