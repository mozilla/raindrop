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

# The outgoing SMTP protocol for raindrop.
from __future__ import with_statement

import sys
from cStringIO import StringIO

from ..proc import base
from . import xoauth

from twisted.internet import protocol, defer
from twisted.python.failure import Failure
from twisted.internet.ssl import ClientContextFactory
from zope.interface import implements

# importing twisted's smtp package generates warnings in 2.6...
if sys.version_info > (2,6):
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from twisted.mail import smtp
from twisted.mail import smtp


from OpenSSL.SSL import SSLv3_METHOD

import logging
logger = logging.getLogger(__name__)


class XOAUTHAuthenticator:
    implements(smtp.IClientAuthentication)
    def getName(self):
        return "XOAUTH"

    def challengeResponse(self, secret, chal):
        logger.info("logging into SMTP server via oauth")
        return secret


SMTPPostingClient_Base=smtp.ESMTPSender
class SMTPPostingClient(SMTPPostingClient_Base): #smtp.ESMTPClient):

    requireAuthentication = False
    requireTransportSecurity = False

    def __init__(self, acct, src_doc, out_doc, *args, **kw):
        self.acct = acct
        self.src_doc = src_doc
        self.out_doc = out_doc
        self.data_file = None # setup later.
        self.seen_from = False
        self.done_sent_state = False
        SMTPPostingClient_Base.__init__(self, *args, **kw)
        self.heloFallback = 1

    def connectionLost(self, reason=protocol.connectionDone):
        # If the connection just dropped without an error response, we
        # will not have called out handlers.
        @defer.inlineCallbacks
        def check_state():
            if not self.done_sent_state:
                _ = yield self._update_sent_state(-1, "Lost connection to server")
        def do_base(result):
            SMTPPostingClient_Base.connectionLost(self, reason)

        d = check_state()
        self.deferred.callback(None)
        d.addCallback(do_base)

    #def smtpTransferFailed(self, code, resp):

    # We use the smtp 'state' functions to work with couch using deferreds -
    # all the 'normal' override points are expecting normal sync results...
    def smtpState_from(self, code, resp):
        # Here we record the fact we have attempted an SMTP send and
        # save the state back now - this should cause conflict errors if we
        # accidently have 2 processes trying to send the same message.
        # XXX - deterministic revision IDs may mean we need a UUID or
        # something too?
        @defer.inlineCallbacks
        def do_couchy():
            dm = self.acct.doc_model
            try:
                _ = yield self.acct._update_sent_state(self.src_doc, 'sending')
                # And now is also a good (enough) time to do a 'deferred' open
                # of the attachment.
                aname, _ = dm.get_schema_attachment_info(self.out_doc,
                                                         'smtp_body')
                attach = yield dm.db.openDoc(dm.quote_id(self.out_doc['_id']),
                                             attachment=aname)
                self.data_file = StringIO(attach)
            except:
                logger.error("Failed to talk to couch\n%s",
                             Failure().getTraceback())
                self._disconnectFromServer()
                raise

        def do_base(result):
            if isinstance(result, Failure):
                logger.error("Failed to update couch state: %s", result)
                self._disconnectFromServer()
            else:
                SMTPPostingClient_Base.smtpState_from(self, code, resp)

        if self.seen_from:
            SMTPPostingClient_Base.smtpState_from(self, code, resp)
        else:
            d = do_couchy()
            d.addBoth(do_base)

    @defer.inlineCallbacks
    def _update_sent_state(self, code, resp):
        # check there isn't a path - particularly error handling - which calls
        # us twice.
        assert not self.done_sent_state, self.src_doc
        # been sent - record that.
        dm = self.acct.doc_model
        # ack - errors talking to couch here are too late to do anything
        # about...
        if code==250:
            _ = yield self.acct._update_sent_state(self.src_doc, 'sent')
        else:
            reason = (code, resp)
            message = resp # theoretically already suitable for humans.
            # for now, reset 'outgoing_state' back to 'outgoing' so the
            # next attempt retries.  We should differentiate between
            # 'permanent' errors and others though...
            _ = yield self.acct._update_sent_state(self.src_doc, 'error',
                                                   reason, message,
                                                   outgoing_state='outgoing')
        self.done_sent_state = True

    def smtpState_msgSent(self, code, resp):
        @defer.inlineCallbacks
        def do_couchy():
            _ = yield self._update_sent_state(code, resp)

        def do_base(result):
            SMTPPostingClient_Base.smtpState_msgSent(self, code, resp)
        d = do_couchy()
        d.addBoth(do_base)

    def getMailFrom(self):
        if self.seen_from:
            # This appears the official way to finish...
            return None
        self.seen_from = True
        return self.out_doc['smtp_from']

    def getMailTo(self):
        return self.out_doc['smtp_to']

    def getMailData(self):
        return self.data_file

    def sendError(self, exc):
        # This will prevent our 'sent' handler about being called, so update
        # the state here.
        def do_base(result):
            SMTPPostingClient_Base.sendError(self, exc)

        d = self._update_sent_state(exc.code, exc.resp)
        d.addCallback(do_base)

    def _registerAuthenticators(self):
        acct_det = self.acct.details
        if xoauth.AcctInfoSupportsOAuth(acct_det):
            logger.info("making OAuth authentication available for account %(id)r",
                        acct_det)
            xoauth_string = xoauth.GenerateXOauthStringFromAcctInfo('smtp', acct_det)
            # twisted is dumb - if no 'secret' (ie, password) is available it
            # doesn't bother to call the authenticators - so we stash it here
            # in the knowledge we do not fall back to password-based login
            # (as we haven't called the base-class)
            self.secret = xoauth_string
            self.registerAuthenticator(XOAUTHAuthenticator())
            # and do *not* register the default authenticators.
        else:
            # register the default password-based authenticators.
            SMTPPostingClient_Base._registerAuthenticators(self)


class SMTPClientFactory(protocol.ClientFactory):
    protocol = SMTPPostingClient

    def __init__(self, account, conductor, src_doc, out_doc,
                 retries=5, timeout=None):
        # base-class has no __init__
        self.src_doc = src_doc
        self.out_doc = out_doc
        self.account = account
        self.conductor = conductor
        self.result = defer.Deferred() # client does errback on this
        def some_result(result):
            if isinstance(result, Failure):
                # XXX - by default this will create a log, which may include
                # a base64 encoded password.
                logger.info('smtp request FAILED: %s', result)
            else:
                logger.info('smtp mail succeeded')

        self.result.addBoth(some_result)
        # These are attributes twisted expects the factory to have.  But
        # we don't use their SMTPSenderFactory as it doesn't work that well
        # with our model of 'read attach and write state as late as possible'
        self.sendFinished = 0

        self.retries = -retries
        self.timeout = timeout

    def buildProtocol(self, addr):
        cf = ClientContextFactory()
        cf.method = SSLv3_METHOD
        p = self.protocol(self.account, self.src_doc, self.out_doc,
                          self.account.details.get("username", ""),
                          self.account.details.get("password", ""),
                          cf,
                          None, # identify???
                          logsize=30,
                          )
        p.deferred = self.deferred # ???????????
        p.factory = self
        return p

    def connect(self):
        details = self.account.details
        logger.debug('attempting to connect to %s:%d (ssl: %s)',
                     details['host'], details['port'], details['ssl'])
        reactor = self.conductor.reactor
        self.deferred = defer.Deferred()
        reactor.connectTCP(details['host'], details['port'], self)
        return self.deferred


class SMTPAccount(base.AccountBase):
    rd_outgoing_schemas = ['rd.msg.outgoing.smtp']
    @defer.inlineCallbacks
    def startSend(self, conductor, src_doc, dest_doc):
        # do it...
        assert src_doc['outgoing_state'] == 'outgoing', src_doc # already sent?
        factory = SMTPClientFactory(self, conductor, src_doc, dest_doc)
        _ = yield factory.connect()
        _ = yield factory.result
        # apparently all done!
        defer.returnValue(True)

    def get_identities(self):
        username = self.details.get('username')
        if '@' not in username:
            logger.warning("SMTP account username isn't an email address - can't guess your identity")
            return []
        return [('email', username)]
