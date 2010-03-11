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

import logging
import time

from twisted.internet import reactor, defer
from twisted.python.failure import Failure
import twisted.web.error
import twisted.internet.error
import paisley

from . import proto as proto
from .config import get_config

logger = logging.getLogger(__name__)

from .model import get_doc_model

# XXX - we need a registry of 'outgoing source docs'.  As all of these
# are actually defined by extensions, we could have a flag on extensions to
# indicate if they are doing outgoing work, then we could determine this list.
source_schemas = ['rd.msg.outgoing.simple',
                  'rd.msg.seen',
                  'rd.msg.deleted',
# archived disabled until we know what to do with them.
#                  'rd.msg.archived',
                  ]


def get_conductor(pipeline):
  conductor = SyncConductor(pipeline)
  # We used to *need* a deferred here - but now initialize doesn't use
  # deferred we don't.  However, we stick with returning a deferred for the
  # sake of not yet adjusting the consumers of this (mainly in the tests)
  conductor.initialize()
  return defer.succeed(conductor)


# XXX - rename this to plain 'Conductor' and move to a different file.
# This 'conducts' synchronization, the work queues and the interactions with
# the extensions and database.
class SyncConductor(object):
  def __init__(self, pipeline):
    self.pipeline = pipeline
    # apparently it is now considered 'good form' to pass reactors around, so
    # a future of multiple reactors is possible.
    # We capture it here, and all the things we 'conduct' use this reactor
    # (but later it should be passed to our ctor too)
    self.reactor = reactor
    self.doc_model = get_doc_model()

    self.accounts_syncing = []
    self.accounts_listening = set()
    self.outgoing_handlers = None
    self.all_accounts = None
    self.calllaters_waiting = {} # keyed by ID, value is a IDelayedCall
    self.deferred = None
    self.num_new_items = None

  def _ohNoes(self, failure, *args, **kwargs):
    logger.error('OH NOES! failure! %s', failure)

  def initialize(self):
    self._load_accounts()
    # ask the pipeline to tell us when new source schemas arrive.
    def new_processor(src_id, src_rev):
      # but our processor doesn't actually process it - it just schedules
      # another 'check outgoing'.
      logger.debug("saw new document %r (%s) - kicking outgoing check",
                   src_id, src_rev)
      self.reactor.callLater(0, self._do_sync_outgoing)
      return [], False

    # we start listening on all accounts - this needs to be optional
    # or explicitly triggered?
    for accts in self.outgoing_handlers.itervalues():
      for acct in accts:
        self.accounts_listening.add(acct)
    inc = self.pipeline.incoming_processor
    if self.accounts_listening and inc is not None:
      inc.add_processor(new_processor, source_schemas, 'outgoing')

  def get_status_ob(self):
    acct_infos = {}
    for acct in self.all_accounts:
      if acct in self.accounts_syncing:
        state = 'synchronizing'
      elif acct in self.accounts_listening:
        state = 'listening'
      else:
        state = 'idle'

      acct_infos[acct.details['id']] = {
                         'state': state,
                         'status': acct.status,
                         }
    return {'accounts' : acct_infos}

  def _load_accounts(self):
    # We used to store account info (other than the password) in couch docs.
    # This creates a hole whereby someone could replace the 'host' name in
    # couchdb with a server under their control, then harvest the password
    # as we attempt to login.
    # We now use only the file-system for account info (and our entry-points
    # for changing account info always updates the password when things are
    # changed)
    # XXX - this still needs work though, as the details are only read
    # once and not updated.  This should be OK in the short-term though, as
    # out sync process does it's job then terminates, so subsequent runs
    # will get new details.
    # get all accounts from the couch.
    assert self.all_accounts is None, "only call me once."
    self.all_accounts = []
    self.outgoing_handlers = {}
    for acct_name, acct_info in get_config().accounts.iteritems():
      acct_id = acct_info['id']
      if not acct_info.get('enabled', True):
        logger.info("account %r is disabled", acct_id)
        continue
      try:
          account_proto = acct_info['proto']
          logger.debug("Found account using protocol %s", account_proto)
      except KeyError:
          logger.error("account %(id)r has no protocol specified - ignoring",
                       acct_info)
          continue
      if account_proto in proto.protocols:
        account = proto.protocols[account_proto](self.doc_model, acct_info)
        logger.debug('loaded %s account: %s', account_proto,
                     acct_info.get('name', acct_id))
        self.all_accounts.append(account)
        # Can it handle any 'outgoing' schemas?
        out_schemas = account.rd_outgoing_schemas
        for sid in (out_schemas or []):
          existing = self.outgoing_handlers.setdefault(sid, [])
          existing.append(account)
      else:
        logger.error("Don't know what to do with account protocol: %s",
                     account_proto)

  def _get_specified_accounts(self, options):
    assert self.all_accounts # no accounts loaded?
    ret = []
    for acct in self.all_accounts:
      proto = acct.details['proto']
      if not options.protocols or proto in options.protocols:
        ret.append(acct)
      else:
          logger.info("Skipping account %r - protocol '%s' is disabled",
                      acct.details['id'], proto)
    return ret

  @defer.inlineCallbacks
  def _process_outgoing_row(self, row):
    if not self.outgoing_handlers:
      logger.warn("ignoring outgoing row - no handlers")
      return

    val = row['value']
    # push it through the pipeline.
    new_items = [(row['id'], val['_rev'], val['rd_schema_id'], None)]
    out_id, out_rev, out_sch = yield self.pipeline.process_until(new_items,
                                                   self.outgoing_handlers)
    if out_id is None:
      logger.warn("doc %r didn't create any outgoing schema items", row['id'])
      return

    logger.info('found outgoing message with schema %s', out_sch)
    # open the original source doc and the outgoing schema we just found.
    dids = [row['id'], out_id]
    src_doc, out_doc = yield self.doc_model.open_documents_by_id(dids)
    if src_doc['_rev'] != val['_rev']:
      raise RuntimeError('the document changed since it was processed.')
    senders = self.outgoing_handlers[out_sch]
    # There may be multiple senders, but first one to process it wins
    # (eg, outgoing imap items have one per account, but each account may be
    # passed one for a different account - it just ignores it, so we continue
    # the rest of the accounts until one says "yes, it is mine!")
    for sender in senders:
      if sender in self.accounts_listening:
        d = sender.startSend(self, src_doc, out_doc)
        if d is not None:
          # This sender accepted the item...
          _ = yield d
          break

  @defer.inlineCallbacks
  def _do_sync_outgoing(self):
    keys = []
    for ss in source_schemas:
      keys.append([ss, 'outgoing_state', 'outgoing'])

    dl = []
    result = yield self.doc_model.open_view(keys=keys, reduce=False)
    for row in result['rows']:
      logger.info("found outgoing document %(id)r", row)
      try:
        def_done = self._process_outgoing_row(row)
        dl.append(def_done)
      except Exception:
        logger.error("Failed to process doc %r\n%s", row['id'],
                     Failure().getTraceback())
    defer.returnValue(dl)

  def sync(self, options, incoming=True, outgoing=True):
    dl = []
    if outgoing:
      dl.append(self.sync_outgoing(options))
    if incoming:
      dl.append(self.sync_incoming(options))
    return defer.DeferredList(dl)

  @defer.inlineCallbacks
  def provide_schema_items(self, items):
    _ = yield self.pipeline.provide_schema_items(items)
    self.num_new_items += len(items)

  @defer.inlineCallbacks
  def sync_outgoing(self, options):
      # start looking for outgoing schemas to sync...
      dl = (yield self._do_sync_outgoing())
      _ = yield defer.DeferredList(dl)

  @defer.inlineCallbacks
  def _record_sync_status(self, result):
    rd_key = ["raindrop", "sync-status"]
    schema_id = 'rd.core.sync-status'
    # see if an existing schema exists to get the existing number.
    si = (yield self.doc_model.open_schemas([(rd_key, schema_id)]))[0]
    num_syncs = 0 if si is None else si['num_syncs']

    # a timestamp in UTC
    items = {'timestamp': time.mktime(time.gmtime()),
             'new_items': self.num_new_items,
             'num_syncs': num_syncs + 1,
    }
    si = {'rd_key': rd_key,
          'rd_schema_id': schema_id,
          'rd_source': None,
          'rd_ext_id': 'rd.core',
          'items': items,
    }
    _ = yield self.pipeline.provide_schema_items([si])
    self.num_new_items = None

  def sync_incoming(self, options):
    assert self.num_new_items is None # eek - we didn't reset correctly...
    self.num_new_items = 0
    if self.deferred is None:
      self.deferred = defer.Deferred()
      self.deferred.addCallback(self._record_sync_status)
    # start synching all 'incoming' accounts.
    accts = self._get_specified_accounts(options)
    for account in accts:
      if account in self.accounts_syncing:
        logger.info("skipping acct %(id)s - already synching...",
                    account.details)
        continue
      # cancel an old 'callLater' if one is scheduled.
      try:
        self.calllaters_waiting.pop(account.details['id']).cancel()
      except (KeyError, twisted.internet.error.AlreadyCalled):
        # either not in the map, or is in the map but we are being called
        # because of it firing.  Note the 'pop' works even if we are
        # already called.
        pass
      # start synching
      logger.info('Starting sync of %s account: %s',
                  account.details['proto'],
                  account.details.get('name', '(un-named)'))
      def_done = account.startSync(self, options)
      if def_done is not None:
        self.accounts_syncing.append(account)
        def_done.addBoth(self._cb_sync_finished, account, options)

    # return a deferred that fires once everything is completely done.
    ret = self.deferred
    self._check_if_finished()
    return ret

  def _cb_sync_finished(self, result, account, options):
    acct_id = account.details['id']
    if isinstance(result, Failure):
      logger.error("Account %s failed with an error: %s", account, result)
      if options.stop_on_error:
        logger.info("--stop-on-error specified - re-throwing error")
        result.raiseException()
    else:
      logger.debug("Account %r finished successfully", acct_id)
      if options.repeat_after:
        logger.info("account %r finished - rescheduling for %d seconds",
                    acct_id, options.repeat_after)
        cl = self.reactor.callLater(options.repeat_after, self.sync, options)
        self.calllaters_waiting[acct_id] = cl

    assert account in self.accounts_syncing, (account, self.accounts_syncing)
    self.accounts_syncing.remove(account)
    self._check_if_finished()

  def _check_if_finished(self):
    if not self.accounts_syncing and not self.calllaters_waiting:
      logger.info("all incoming accounts have finished synchronizing")
      d = self.deferred
      self.deferred = None
      d.callback(None)
