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

from twisted.internet import protocol, ssl, defer, error, task
from twisted.mail import imap4
from twisted.python.failure import Failure
from zope.interface import implements

import logging
from email.utils import mktime_tz, parsedate_tz
import time
import re
import base64

from ..proc import base
from ..model import DocumentSaveError
from . import xoauth

brat = base.Rat

logger = logging.getLogger(__name__)

# Set this to see IMAP lines printed to the console.
# NOTE: lines printed may include your password!
TRACE_IMAP = False

NUM_QUERYERS = 3
NUM_FETCHERS = 3

# magic numbers.  Some should probably come from the account info...
NUM_CONNECT_RETRIES = 4
RETRY_BACKOFF = 8
MAX_BACKOFF = 600
# timeouts should be rare - the server may be slow!  Errors are more likely
# to be connection drops - so we leave this timeout fairly high...
DEFAULT_TIMEOUT = 60*5
# we fetch this many bytes or this many messages, whichever we hit first.
MAX_BYTES_PER_FETCH = 500000
MAX_MESSAGES_PER_FETCH = 30


def log_exception(msg, *args):
  # inlineCallbacks don't work well with the logging module's handling of
  # exceptions - we need to use the Failure() object...
  msg = (msg % args) + "\n" + Failure().getTraceback()
  logger.error(msg)

def get_rdkey_for_email(msg_id):
  # message-ids must be consistent everywhere we use them, and we decree
  # the '<>' is stripped (if for no better reason than the Python email
  # package's 'unquote' function will strip them by default...
  if msg_id.startswith("<") and msg_id.endswith(">"):
    msg_id = msg_id[1:-1]
  return ("email", msg_id)

# Finding an imap ENVELOPE structure with non-character data isn't good -
# couch can't store it (except in attachments) and we can't do anything with
# it anyway.  It *appears* from the IMAP spec that only 7bit data is valid,
# so that is what we check
def check_envelope_ok(env):
  # either strings, or (nested) lists of strings.
  def flatten(what):
    ret = []
    for item in what:
      if item is None:
        pass
      elif isinstance(what, str):
        ret.append(what)
      elif isinstance(what, list):
        ret.extend(flatten(item))
      else:
        raise TypeError, what
    return ret

  for item in flatten(env):
    try:
      item.encode('ascii')
    except UnicodeError:
      return False
  return True


class XOAUTHAuthenticator:
    implements(imap4.IClientAuthentication)

    def getName(self):
      return "XOAUTH"

    def challengeResponse(self, secret, chal):
      return secret


class ImapClient(imap4.IMAP4Client):
  timeout = DEFAULT_TIMEOUT
  _in_auth = False
  def _defaultHandler(self, tag, rest):
    # XXX - worm around a bug related to MismatchedQuoting exceptions.
    # Probably: http://twistedmatrix.com/trac/ticket/1443
    # "[imap4] mismatched quoting spuriously raised" - raised early 2006 :(
    try:
      imap4.IMAP4Client._defaultHandler(self, tag, rest)
    except imap4.MismatchedQuoting, exc:
      logger.warn('ignoring mismatched quoting error: %s', exc)
      # The rest seems necessary to 'gracefully' ignore the error.
      cmd = self.tags[tag]
      cmd.defer.errback(exc)
      del self.tags[tag]
      self.waiting = None
      self._flushQueue()
      # *sob* - but it doesn't always do a great job at ignoring them - most
      # other handlers of imap4.IMAP4Exceptions are also handling this :(

  def timeoutConnection(self):
    logger.warn("IMAP connection timed out")
    return imap4.IMAP4Client.timeoutConnection(self)

  @defer.inlineCallbacks
  def serverGreeting(self, _):
    #logger.debug("IMAP server greeting: capabilities are %s", caps)
    caps = yield self.getCapabilities()
    if 'AUTH' in caps and 'XOAUTH' in caps['AUTH']:
      acct_det = self.account.details
      if xoauth.AcctInfoSupportsOAuth(acct_det):
        self.registerAuthenticator(XOAUTHAuthenticator())
        logger.info("logging into account %r via oauth", acct_det['id'])
        # do the xoauth magic.
        xoauth_string = xoauth.GenerateXOauthStringFromAcctInfo('imap', acct_det)
        self._in_auth = True
        try:
          _ = yield self.authenticate(xoauth_string)
        finally:
          self._in_auth = False
        # it isn't clear why we need to explicitly call the deferred callback
        # here when we don't for login - but whateva...
        self.deferred.callback(self)
        return
      else:
        logger.warn("This server supports OAUTH but no tokens or secrets are available to use - falling back to password")
    _ = yield self._startLogin()

  def _startLogin(self):
    if self.account.details.get('crypto') == 'TLS':
      d = self.startTLS(self.factory.ctx)
      d.addCallback(self._doLogin)
    else:
      d = self._doLogin()
    def done(result):
      td, self.deferred = self.deferred, None
      if isinstance(result, Failure):
        # throw the connection away - we will (probably) retry...
        def fire_errback(_):
          td.errback(result)
        defer.maybeDeferred(self.transport.loseConnection
                           ).addBoth(fire_errback)
      else:
        td.callback(self)
    return d.addBoth(done)

  def _doLogin(self, *args, **kwargs):
    return self.login(self.account.details['username'].encode('imap4-utf-7'),
                      self.account.details['password'])

  def xlist(self, reference, wildcard):
    # like 'list', but does XLIST.  Caller is expected to have checked the
    # server offers this capability.
    cmd = 'XLIST'
    args = '"%s" "%s"' % (reference, wildcard.encode('imap4-utf-7'))
    resp = ('XLIST',)
    # Have I mentioned I hate the twisted IMAP client yet today?
    # Tell the Command class about the new XLIST command...
    cmd = imap4.Command(cmd, args, wantResponse=resp)
    cmd._1_RESPONSES = cmd._1_RESPONSES  + ('XLIST',)
    d = self.sendCommand(cmd)
    d.addCallback(self.__cbXList, 'XLIST')
    return d

  # *sob* - duplicate the callback due to twisted using private '__'
  # attributes...
  def __cbXList(self, (lines, last), command):
    results = []
    for L in lines:
        parts = imap4.parseNestedParens(L)
        if len(parts) != 4:
            raise imap4.IllegalServerResponse, L
        if parts[0] == command:
            parts[1] = tuple(parts[1])
            results.append(tuple(parts[1:]))
    return results

  def sendLine(self, line):
    # twisted has a bug where it base64 encodes our xoauth secret, and while
    # it strips \n chars from the end, it does *not* remove them from the
    # middle.  So we have around this here...
    if self._in_auth:
      line = line.replace("\n", "")
    # and support our tracing.
    if TRACE_IMAP:
      print 'C: %08x: %s' % (id(self), repr(line))
    return imap4.IMAP4Client.sendLine(self, line)
  
  if TRACE_IMAP:
    def lineReceived(self, line):
      if len(line) > 50:
        lrepr = repr(line[:50]) + (' <+ %d more bytes>' % len(line[50:]))
      else:
        lrepr = repr(line)
      print 'S: %08x: %s' % (id(self), lrepr)
      return imap4.IMAP4Client.lineReceived(self, line)


class ImapProvider(object):
  # The 'id' of this extension
  # XXX - should be managed by our caller once these 'protocols' become
  # regular extensions.
  rd_extension_id = 'proto.imap'

  def __init__(self, account, conductor, options):
    self.account = account
    self.options = options
    self.conductor = conductor
    self.doc_model = account.doc_model
    # We have a couple of queues to do the work
    self.query_queue = defer.DeferredQueue() # IMAP folder etc query requests 
    self.fetch_queue = defer.DeferredQueue() # IMAP message fetch requests
    self.updated_folder_infos = None

  @defer.inlineCallbacks
  def write_items(self, items):
    try:
      if items:
        _ = yield self.conductor.provide_schema_items(items)
    except DocumentSaveError, exc:
      # So - conflicts are a fact of life in this 'queue' model: we check
      # if a record exists and it doesn't, so we queue the write.  By the
      # time the write gets processed, it may have been written by a
      # different extension...
      conflicts = []
      for info in exc.infos:
        if info['error']=='conflict':
          # The only conflicts we are expecting are creating the rd.msg.rfc822
          # schema, which arise due to duplicate message IDs (eg, an item
          # in 'sent items' and also the received copy).  Do a 'poor-mans'
          # check that this is indeed the only schema with a problem...
          if not info.get('id', '').endswith('!rd.msg.rfc822'):
            raise
          conflicts.append(info)
        else:
          raise
      if not conflicts:
        raise # what error could this be??
      # so, after all the checking above, a debug log is all we need for this
      logger.debug('ignored %d conflict errors writing this batch (first 3=%r)',
                   len(conflicts), conflicts[:3])

  @defer.inlineCallbacks
  def maybe_queue_fetch_items(self, folder_path, infos):
    if not infos:
      return
    by_uid = yield self._findMissingItems(folder_path, infos)
    if not by_uid:
      return
    self.fetch_queue.put((False, self._processFolderBatch, (folder_path, by_uid)))

  @defer.inlineCallbacks
  def _reqList(self, conn, *args, **kwargs):
    self.account.reportStatus(brat.EVERYTHING, brat.GOOD)
    acct_id = self.account.details.get('id','')
    caps = yield conn.getCapabilities()
    if 'XLIST' in caps:
      result = yield conn.xlist('', '*')
      kind = self.account.details.get('kind','')
      if kind is '':
        logger.warning("set kind=gmail for account %s in your .raindrop for correct settings",
                        acct_id)
    else:
      logger.warning("This IMAP server doesn't support XLIST, so performance may suffer")
      result = yield conn.list('', '*')
    # quickly scan through the folders list building the ones we will
    # process and the order.
    logger.info("examining folders")
    folders_use = []
    # First pass - filter folders we don't care about.
    if 'exclude_folders' in self.account.details:
      to_exclude = set(o.lower() for o in re.split(", *", self.account.details['exclude_folders']))
    else:
      to_exclude = set()
    for flags, delim, name in result:
      name = name.decode('imap4-utf-7') # twisted gives back the encoded str.
      ok = True
      for flag in (r'\Noselect', r'\AllMail', r'\Trash', r'\Spam'):
        if flag in flags:
          logger.debug("'%s' has flag %r - skipping", name, flag)
          ok = False
          break
      if ok and self.options.folders and \
         name.lower() not in [o.lower() for o in self.options.folders]:
        logger.debug('skipping folder %r - not in specified folder list', name)
        ok = False
      if ok and 'exclude_folders' in self.account.details and \
         name.lower() in to_exclude:
        logger.debug('skipping folder %r - in exclude list', name)
        ok = False
      if ok:
        folders_use.append((flags, delim, name ))

    # Second pass - prioritize the folders into the order we want to
    # process them - 'special' ones first in a special order, then remaining
    # top-level folders the order they appear, then sub-folders in the order
    # they appear...
    todo_special_folders = []
    todo_top = []
    todo_sub = []

    if 'XLIST' in caps:
      for flags, delim, name in folders_use:
        folder_info = (delim, name)
        # see if this is a special folder 
        for flag in flags:
          if flag == r'\Inbox':
            # Don't use the localized inbox name when talking to the server.
            # Gmail doesn't like this, for example.
            todo_special_folders.insert(0, (delim, "INBOX"))
            break
          elif flag in (r'\Sent', r'\Drafts'):
            todo_special_folders.append(folder_info)
            break
        else:
          # for loop wasn't broken - not a special folder
          if delim in name:
            todo_sub.append(folder_info)
          else:
            todo_top.append(folder_info)
    else:
      # older mapi server - just try and find the inbox.
      for flags, delim, name in folders_use:
        folder_info = (delim, name)
        if delim in name:
          todo_sub.append(folder_info)
        elif name.lower()=='inbox':
          todo_top.insert(0, folder_info)
        else:
          todo_top.append(folder_info)
    
    todo = todo_special_folders + todo_top + todo_sub
    try:
      _ = yield self._updateFolders(conn, todo)
    except:
      log_exception("Failed to update folders for account %r", acct_id)
    # and tell the query queue everything is done.
    self.query_queue.put(None)

  @defer.inlineCallbacks
  def _checkQuickRecent(self, conn, folder_path, max_to_fetch):
    logger.debug("_checkQuickRecent for %r", folder_path)
    _ = yield conn.select(folder_path)
    nitems = yield conn.search("((OR UNSEEN (OR RECENT FLAGGED))"
                               " UNDELETED SMALLER 50000)", uid=True)
    if not nitems:
      logger.debug('folder %r has no quick items', folder_path)
      return
    nitems = nitems[-max_to_fetch:]
    batch = imap4.MessageSet(nitems[0], nitems[-1])
    results = yield conn.fetchAll(batch, uid=True)
    logger.info('folder %r has %d quick items', folder_path, len(results))
    # Make a simple list.
    infos = [results[seq] for seq in sorted(int(k) for k in results)
             if self.shouldFetchMessage(results[seq])]
    _ = yield self.maybe_queue_fetch_items(folder_path, infos)

  @defer.inlineCallbacks
  def _updateFolders(self, conn, all_names):
    # Fetch all state cache docs for all mailboxes in one go.
    # XXX - need key+schema here, but we don't use multiple yet.
    acct_id = self.account.details.get('id')
    startkey = ['rd.core.content', 'key', ['imap-mailbox', [acct_id]]]
    endkey = ['rd.core.content', 'key', ['imap-mailbox', [acct_id, {}]]]
    results = yield self.doc_model.open_view(startkey=startkey,
                                             endkey=endkey, reduce=False,
                                             include_docs=True)
    # build a map of the docs keyed by folder-name.
    caches = {}
    for row in results['rows']:
      doc = row['doc']
      folder_name = doc['rd_key'][1][1]
      if doc['rd_schema_id'] == 'rd.core.error':
        # ack - failed last time for some reason - skip it.
        continue
      assert doc['rd_schema_id'] in ['rd.imap.mailbox-cache',
                                     'rd.core.error'], doc ## fix me above
      caches[folder_name] = doc
    logger.debug('opened cache documents for %d folders', len(caches))

    # All folders without cache docs get the special 'fetch quick'
    # treatment...
    for delim, name in all_names:
      if name not in caches:
        self.query_queue.put((False, self._checkQuickRecent, (name, 20)))

    # We only update the cache of the folder once all items from that folder
    # have been written, so extensions only run once all items fetched.
    assert not self.updated_folder_infos
    self.updated_folder_infos = []

    for delim, name in all_names:
      self.query_queue.put((False, self._updateFolderFromCache, (caches, delim, name)))

  @defer.inlineCallbacks
  def _updateFolderFromCache(self, conn, cache_docs, folder_delim, folder_name):
    # Now queue the updates of the folders
    acct_id = self.account.details.get('id')
    info = yield conn.select(folder_name)
    logger.debug("info for %r is %r", folder_name, info)

    cache_doc = cache_docs.get(folder_name, {})
    dirty = yield self._syncFolderCache(conn, folder_name, info, cache_doc)

    if dirty:
      logger.debug("need to update folder cache for %r", folder_name)
      items = {'uidvalidity': cache_doc['uidvalidity'],
               'infos': cache_doc['infos']
               }
      new_item = {'rd_key' : ['imap-mailbox', [acct_id, folder_name]],
                  'rd_schema_id': 'rd.imap.mailbox-cache',
                  'rd_ext_id': self.rd_extension_id,
                  'items': items,
      }
      if '_id' in cache_doc:
        new_item['_id'] = cache_doc['_id']
        new_item['_rev'] = cache_doc['_rev']
      self.updated_folder_infos.append(new_item)
      sync_items = cache_doc['infos']
    else:
      sync_items = cache_doc.get('infos')

    # fetch folder info, and delete information about 'stale' locations
    # before fetching the actual messages.
    loc_to_nuke, loc_needed = yield self._makeLocationInfos(folder_name,
                                                            folder_delim,
                                                            sync_items)

    # queue the write of location records we want to nuke first.
    if loc_to_nuke:
      _ = yield self.write_items(loc_to_nuke)

    todo = sync_items[:]
    while todo:
      # do later ones first and limit the batch size - larger batches means
      # fewer couch queries, but the queue appears to 'stall' for longer.
      batch = []
      while len(batch) < 100 and todo:
          mi = todo.pop()
          if self.shouldFetchMessage(mi):
              batch.insert(0, mi)
      logger.log(1, 'queueing check of %d items in %r', len(batch), folder_name)
      _ = yield self.maybe_queue_fetch_items(folder_name, batch)
      # see if these items also need location records...
      new_locs = []
      for mi in batch:
        try:
          new_locs.append(loc_needed[mi['UID']])
        except KeyError:
          pass
      if new_locs:
        logger.debug('queueing %d new location records', len(new_locs))
        _ = yield self.write_items(new_locs)
    # XXX - todo - should nuke old folders which no longer exist.

  @defer.inlineCallbacks
  def _syncFolderCache(self, conn, folder_path, server_info, cache_doc):
    # Queries the server for the current state of a folder.  Returns True if
    # the cache document was updated so needs to be written back to couch.
    suidv = int(server_info['UIDVALIDITY'])
    dirty = False
    if suidv != cache_doc.get('uidvalidity'):
      infos = cache_doc['infos'] = []
      cache_doc['uidvalidity'] = suidv
      dirty = True
    else:
      try:
        infos = cache_doc['infos']
      except KeyError:
        infos = cache_doc['infos'] = []
        dirty = True

    if infos:
      cached_uid_next = int(infos[-1]['UID']) + 1
    else:
      cached_uid_next = 1

    suidn = int(server_info.get('UIDNEXT', -1))

    try:
      if suidn == -1 or suidn > cached_uid_next:
        if suidn == -1:
          logger.warn("This server doesn't provide UIDNEXT - it will take longer to synch...")
        logger.debug('requesting info for items in %r from uid %r', folder_path,
                     cached_uid_next)
        new_infos = yield conn.fetchAll("%d:*" % (cached_uid_next,), True)
      else:
        logger.info('folder %r has no new messages', folder_path)
        new_infos = {}
      # Get flags for all 'old' messages.
      if cached_uid_next > 1:
        updated_flags = yield conn.fetchFlags("1:%d" % (cached_uid_next-1,), True)
      else:
        updated_flags = {}
    except imap4.MismatchedQuoting, exc:
      acct_id = self.account.details.get('id','')
      log_exception("failed to fetchAll/fetchFlags folder %r on account %r",
                    folder_path, acct_id)
      new_infos = {}
      updated_flags = {}
    logger.info("folder %r has %d new items, %d flags for old items",
                folder_path, len(new_infos), len(updated_flags))

    # Turn the dicts back into the sorted-by-UID list it started as, nuking
    # old messages
    infos_ndx = 0
    for seq in sorted(int(k) for k in updated_flags):
      info = updated_flags[seq]
      this_uid = int(info['UID'])
      # remove items which no longer exist.
      while int(infos[infos_ndx]['UID']) < this_uid:
        old = infos.pop(infos_ndx)
        logger.debug('detected a removed imap item %r', old)
        dirty = True
      if int(infos[infos_ndx]['UID']) == this_uid:
        old_flags = infos[infos_ndx].get('FLAGS')
        new_flags = info["FLAGS"]
        if old_flags != new_flags:
          dirty = True
          infos[infos_ndx]['FLAGS'] = new_flags
          logger.debug('new flags for UID %r - were %r, now %r',
                       this_uid, old_flags, new_flags)
        infos_ndx += 1
        # we might get more than we asked for - that's OK - we should get
        # them in 'new_infos' too.
        if infos_ndx >= len(infos):
          break
      else:
        # We see this happen when we previously rejected an item due to
        # invalid or missing ENVELOPE etc.
        logger.debug("message %r never seen before - probably invalid", this_uid)
        continue
    # Records we had in the past now have accurate flags; next up is to append
    # new message info we just received...
    for seq in sorted(int(k) for k in new_infos):
      info = new_infos[seq]
      # Sadly, asking for '900:*' in gmail may return a single item
      # with UID of 899 - and that is already in our list.  So only append
      # new items when they are > then what we know about.
      this_uid = int(info['UID'])
      if this_uid < cached_uid_next:
        continue
      # Some items from some IMAP servers don't have an ENVELOPE record, and
      # lots of later things get upset at that.  It isn't clear what such
      # items are yet...
      try:
        envelope = info['ENVELOPE']
      except KeyError:
        logger.debug('imap item has no envelope - skipping: %r', info)
        continue
      if envelope[-1] is None:
        logger.debug('imap item has no message-id - skipping: %r', info)
        continue
      if not check_envelope_ok(envelope):
        logger.debug('imap info has invalid envelope - skipping: %r', info)
        continue
      # it is good - keep it.
      cached_uid_next = this_uid + 1
      infos.append(info)
      dirty = True
    defer.returnValue(dirty)

  @defer.inlineCallbacks
  def _makeLocationInfos(self, folder_name, delim, results):
    # We used to write all location records - even those we were never going
    # to fetch - in one hit - after fetchng the messages.  For large IMAP
    # accounts, this was unacceptable as too many records hit the couch at
    # once.
    # Note a key requirement here is to fetch new messages quickly, and to
    # perform OK with a new DB.  So, the general process is:
    # * Query all couch items which say they are in this location.
    # * Find the set of messages no longer in this location and delete them
    #   all in one go.
    # * Find the set of messages which we don't have location records for.
    #   As we process and filter each individual message, check this map to
    #   see if a new record needs to be written and write it with the message
    #   itself.
    # This function returns the 2 maps - the caller does the delete/update...
    folder_path = folder_name.split(delim)
    logger.debug("checking what we know about items in folder %r", folder_path)
    acct_id = self.account.details.get('id')
    # Build a map keyed by the rd_key of all items we know are currently in
    # the folder
    current = {}
    for result in results:
      msg_id = result['ENVELOPE'][-1]
      rdkey = get_rdkey_for_email(msg_id)
      current[tuple(rdkey)] = result['UID']

    # We hack the 'extension_id' in a special way to allow multiple of the
    # same schema; multiple IMAP accounts, for example, may mean the same
    # rdkey ends up with multiple of these location records.
    # XXX - this is a limitation in the doc model we should fix!
    ext_id = "%s~%s~%s" % (self.rd_extension_id, acct_id, ".".join(folder_path))

    # fetch all things in couch which (a) are currently tagged with this
    # location and (b) was tagged by this mapi account.  We do (a) via the
    # key param, and filter (b) here...
    key = ['rd.msg.location', 'location', folder_path]
    existing = yield self.doc_model.open_view(key=key, reduce=False,
                                              include_docs=True)
    scouch = set()
    to_nuke = []
    for row in existing['rows']:
      doc = row['doc']
      if doc.get('source') != ['imap', acct_id]:
        # Something in this location, but it was put there by other than
        # this IMAP account - ignore it.
        continue
      rdkey = tuple(doc['rd_key'])
      if rdkey not in current:
        to_nuke.append({'_id': doc['_id'],
                        '_rev': doc['_rev'],
                        '_deleted': True,
                        'rd_ext_id': ext_id,
                        })
      scouch.add(rdkey)

    # Finally find the new ones we need to add
    to_add = {}
    for rdkey in set(current) - scouch:
      # Item in the folder but couch doesn't know it is there.
      uid = current[rdkey]
      new_item = {'rd_key': list(rdkey),
                  'rd_ext_id': ext_id,
                  'rd_schema_id': 'rd.msg.location',
                  'items': {'location': folder_path,
                            'location_sep': delim,
                            'uid': uid,
                            'source': ['imap', acct_id]},
                  }
      to_add[uid] = new_item
    logger.debug("folder %r info needs to update %d and delete %d location records",
                 folder_name, len(to_add), len(to_nuke))
    defer.returnValue((to_nuke, to_add))

  @defer.inlineCallbacks
  def _findMissingItems(self, folder_path, results):
    # Transform a list of IMAP infos into a map with the results keyed by the
    # 'rd_key' (ie, message-id)
    assert results, "don't call me with nothing to do!!"
    msg_infos = {}
    for msg_info in results:
      msg_id = msg_info['ENVELOPE'][-1]
      if msg_id in msg_infos:
        # This isn't a very useful check - we are only looking in a single
        # folder...
        logger.warn("Duplicate message ID %r detected", msg_id)
        # and it will get clobbered below :(
      msg_infos[get_rdkey_for_email(msg_id)] = msg_info

    # Get all messages that already have this schema
    keys = [['rd.core.content', 'key-schema_id', [k, 'rd.msg.rfc822']]
            for k in msg_infos.keys()]
    result = yield self.doc_model.open_view(keys=keys, reduce=False)
    seen = set([tuple(r['value']['rd_key']) for r in result['rows']])
    # convert each key elt to a list like we get from the views.
    remaining = set(msg_infos)-set(seen)

    logger.debug("batch for folder %s has %d messages, %d new", folder_path,
                len(msg_infos), len(remaining))
    rem_uids = [int(msg_infos[k]['UID']) for k in remaining]
    # *sob* - re-invert keyed by the UID.
    by_uid = {}
    for key, info in msg_infos.iteritems():
      uid = int(info['UID'])
      if uid in rem_uids:
        info['RAINDROP_KEY'] = key
        by_uid[uid] = info
    defer.returnValue(by_uid)

  @defer.inlineCallbacks
  def _processFolderBatch(self, conn, folder_path, by_uid):
    """Called asynchronously by a queue consumer"""
    conn.select(folder_path) # should check if it already is selected?
    acct_id = self.account.details.get('id')
    num = 0
    # fetch most-recent (highest UID) first...
    left = sorted(by_uid.keys(), reverse=True)
    while left:
      # do as many as we can each time while staying inside our MAX_*
      # constraints...
      nbytes = 0
      this = []
      while left and len(this) < MAX_MESSAGES_PER_FETCH and nbytes < MAX_BYTES_PER_FETCH:
        look = left.pop(0)
        this.append(look)
        try:
          this_bytes = int(by_uid[look]['RFC822.SIZE'])
        except (KeyError, ValueError):
          logger.info("invalid message size in`%r", by_uid[look])
          this_bytes = 100000 # whateva...
        nbytes += this_bytes
      logger.debug("starting fetch of %d items from %r (%d bytes)",
                   len(this), folder_path, nbytes)
      to_fetch = ",".join(str(v) for v in this)
      # We need to use fetchSpecific so we can 'peek' (ie, not reset the
      # \\Seen flag) - note that gmail does *not* reset the \\Seen flag on
      # a fetchMessages, but rfc-compliant servers do...
      results = yield conn.fetchSpecific(to_fetch, uid=True, peek=True)
      logger.debug("fetch from %r got %d", folder_path, len(results))
      #results = yield conn.fetchMessage(to_fetch, uid=True)
      # Run over the results stashing in our by_uid dict.
      infos = []
      for info in results.values():
        # hrmph - fetchSpecific's return value is undocumented and strange!
        assert len(info)==1
        uidlit, uid, bodylit, req_data, content = info[0]
        assert uidlit=='UID'
        assert bodylit=='BODY'
        assert not req_data, req_data # we didn't request headers etc.
        uid = int(uid)
        # but if we used fetchMessage:
        #   uid = int(info['UID'])
        #   content = info['RFC822']
        flags = by_uid[uid]['FLAGS']
        rdkey = by_uid[uid]['RAINDROP_KEY']
        mid = rdkey[-1]
        # XXX - we need something to make this truly unique.
        logger.debug("new imap message %r (flags=%s)", mid, flags)
  
        # put our schemas together
        attachments = {'rfc822' : {'content_type': 'message',
                                   'data': content,
                                   }
        }
        infos.append({'rd_key' : rdkey,
                      'rd_ext_id': self.rd_extension_id,
                      'rd_schema_id': 'rd.msg.rfc822',
                      'items': {},
                      'attachments': attachments,})
      num += len(infos)
      _ = yield self.write_items(infos)
    defer.returnValue(num)

  def shouldFetchMessage(self, msg_info):
    if "\\deleted" in [f.lower() for f in msg_info['FLAGS']]:
      logger.debug("msg is deleted - skipping: %r", msg_info)
      return False
    if self.options.max_age:
      # XXX - we probably want the 'internal date'...
      date_str = msg_info['ENVELOPE'][0]
      try:
        date = mktime_tz(parsedate_tz(date_str))
      except (ValueError, TypeError):
        return False # invalid date - skip it.
      if date < time.time() - self.options.max_age:
        logger.log(1, 'skipping message - too old')
        return False
    if not msg_info['ENVELOPE'][-1]:
      logger.debug("msg has no message ID - skipping: %r", msg_info)
      return False
    return True


class ImapUpdater:
  def __init__(self, account, conductor):
    self.account = account
    self.conductor = conductor
    self.doc_model = account.doc_model

  # Outgoing items related to IMAP - eg, \\Seen flags, deleted, etc...
  @defer.inlineCallbacks
  def handle_outgoing(self, conductor, src_doc, dest_doc):
    account = self.account
    # Establish a connection to the server
    logger.debug("setting flags for %(rd_key)r: folder %(folder)r, uuid %(uid)s",
                 dest_doc)
    client = yield get_connection(account, conductor)
    _ = yield client.select(dest_doc['folder'])
    # Write the fact we are about to try and (un-)set the flag.
    _ = yield account._update_sent_state(src_doc, 'sending')
    try:
      try:
        flags_add = dest_doc['flags_add']
      except KeyError:
        pass
      else:
        client.addFlags(dest_doc['uid'], flags_add, uid=1)
      try:
        flags_rem = dest_doc['flags_remove']
      except KeyError:
        pass
      else:
        client.removeFlags(dest_doc['uid'], flags_rem, uid=1)
    except imap4.IMAP4Exception, exc:
      logger.error("Failed to update flags: %s", fun, exc)
      # XXX - we need to differentiate between a 'fatal' error, such as
      # when the message has been deleted, or a transient error which can be
      # retried.  For now, assume retryable...
      _ = yield account._update_sent_state(src_doc, 'error', exc,
                                           outgoing_state='outgoing')
    else:
      _ = yield account._update_sent_state(src_doc, 'sent')
      logger.debug("successfully adjusted flags for %(rd_key)r", src_doc)
    client.logout()


def failure_to_status(failure):
  exc = failure.value
  if isinstance(exc, error.ConnectionRefusedError):
    why = brat.UNREACHABLE
  elif isinstance(exc, imap4.IMAP4Exception):
    # XXX - better detection is needed!
    why = brat.PASSWORD
  elif isinstance(exc, error.TimeoutError):
    why = brat.TIMEOUT
  else:
    why = brat.UNKNOWN
  return {'what': brat.SERVER,
          'state': brat.BAD,
          'why': why,
          'message': failure.getErrorMessage()}

def get_connection(account, conductor):
    ready = defer.Deferred()
    _do_get_connection(account, conductor, ready, NUM_CONNECT_RETRIES, RETRY_BACKOFF)
    return ready


@defer.inlineCallbacks  
def _do_get_connection(account, conductor, ready, retries_left, backoff):
    this_ready = defer.Deferred()
    factory = ImapClientFactory(account, conductor, this_ready)
    factory.connect()
    try:
      conn = yield this_ready
      # yay - report we are good and tell the real callback we have it.
      account.reportStatus(brat.EVERYTHING, brat.GOOD)
      ready.callback(conn)
    except Exception, exc:
      fail = Failure()
      logger.debug("first chance connection error handling: %s\n%s",
                   fail.getErrorMessage(), fail.getBriefTraceback())
      retries_left -= 1
      if retries_left <= 0:
        ready.errback(fail)
      else:
        status = failure_to_status(fail)
        account.reportStatus(**status)
        acct_id = account.details.get('id','')
        logger.warning('Failed to connect to account %r, will retry after %s secs: %s',
                       acct_id, backoff, fail.getErrorMessage())
        next_backoff = min(backoff * 2, MAX_BACKOFF) # magic number
        conductor.reactor.callLater(backoff,
                                    _do_get_connection,
                                    account, conductor, ready,
                                    retries_left, next_backoff)


class ImapClientFactory(protocol.ClientFactory):
  protocol = ImapClient
  def __init__(self, account, conductor, def_ready):
    # base-class has no __init__
    self.account = account
    self.conductor = conductor
    self.doc_model = account.doc_model # this is a little confused...
    # The deferred triggered after connection and the greeting/auth handshake
    self.def_ready = def_ready
    self.ctx = ssl.ClientContextFactory()

  def buildProtocol(self, addr):
    p = self.protocol(self.ctx)
    p.factory = self
    p.account = self.account
    p.doc_model = self.account.doc_model
    p.deferred = self.def_ready
    return p

  def clientConnectionFailed(self, connector, reason):
    logger.debug("clientConnectionFailed: %s", reason)
    d, self.def_ready = self.def_ready, None
    d.errback(reason)

  def clientConnectionLost(self, connector, reason):
    # this is called even for 'normal' and explicit disconnections; debug
    # logging might help diagnose other problems though...
    logger.debug("clientConnectionLost: %s", reason)

  def connect(self):
    details = self.account.details
    host = details.get('host')
    is_gmail = details.get('kind')=='gmail'
    if not host and is_gmail:
      host = 'imap.gmail.com'
    if not host:
      raise ValueError, "this account has no 'host' configured"

    ssl = details.get('ssl')
    if ssl is None and is_gmail:
      ssl = True
    port = details.get('port')
    if not port:
      port = 993 if ssl else 143

    logger.debug('attempting to connect to %s:%d (ssl: %s)', host, port, ssl)
    reactor = self.conductor.reactor
    if ssl:
      ret = reactor.connectSSL(host, port, self, self.ctx)
    else:
      ret = reactor.connectTCP(host, port, self)
    return ret


class IMAPAccount(base.AccountBase):
  rd_outgoing_schemas = ['rd.proto.outgoing.imap-flags']
  def startSend(self, conductor, src_doc, dest_doc):
    # Check it really is for our IMAP account.
    if dest_doc['account'] != self.details.get('id'):
      logger.info('outgoing item not for imap acct %r (target is %r)',
                  self.details.get('id'), dest_doc['account'])
      return
    # caller should check items are ready to send.
    assert src_doc['outgoing_state'] == 'outgoing', src_doc
    # We know IMAP currently only has exactly 1 outgoing schema type.
    assert dest_doc['rd_schema_id'] == 'rd.proto.outgoing.imap-flags', src_doc

    updater = ImapUpdater(self, conductor)
    return updater.handle_outgoing(conductor, src_doc, dest_doc)

  @defer.inlineCallbacks
  def startSync(self, conductor, options):
    prov = ImapProvider(self, conductor, options)

    @defer.inlineCallbacks
    def consume_connection_queue(q, def_done, retries_left=None, backoff=None):
      if retries_left is None: retries_left = NUM_CONNECT_RETRIES
      if backoff is None: backoff = RETRY_BACKOFF
      try:
        _ = yield _do_consume_connection_queue(q, def_done, retries_left, backoff)
      except GeneratorExit: # ahh, the joys of twisted and errors...
        logger.info("connection queue terminating prematurely")
        def_done.errback(Failure())
      except Exception:
        # We only get here when all retries etc have failed and a queue has
        # given up for good.
        acct_id = self.details.get('id','')
        log_exception("failed to process a queue for account %r", acct_id)
        def_done.errback(Failure())

    @defer.inlineCallbacks
    def _do_consume_connection_queue(q, def_done, retries_left, backoff):
      """Processes the query queue."""
      conn = None
      try:
        while True:
          result = yield q.get()
          if result is None:
            logger.debug('queue processor stopping')
            q.put(None) # tell other consumers to stop
            def_done.callback(None)
            break
          seeder, func, xargs = result
          # else a real item to process.
          if conn is None or conn.tags is None:
            if conn is not None:
              logger.warn('unexpected IMAP connection failure - reconnecting')
            # getting the initial connection has its own retry semantics
            try:
              conn = yield get_connection(self, conductor)
              assert conn.tags is not None, "got disconnected connection??"
            except:
              # can't get a connection - must re-add the item to the queue
              # then re-throw the error back out so this queue stops.
              if seeder:
                q.put(None) # give up and let all consumers stop.
              else:
                q.put(result) # retry - if we wind up failing later, big deal...
              raise
          try:
            args = (conn,) + xargs
            _ = yield func(*args)
            # if we got this far we successfully processed an item - report that.
            self.reportStatus(brat.EVERYTHING, brat.GOOD)
            # It is possible the server disconnected *after* sending a
            # response - handle it here so we get a more specific log message.
            if conn.tags is None:
              logger.warn("unexpected connection failure after calling %r", func)
              logger.log(1, "arguments were %s", xargs)
              self.reportStatus(brat.SERVER, brat.BAD)
              conn = None
          except imap4.IMAP4Exception, exc:
            # put the item back in the queue for later or for another successful
            # connection.
            q.put(result)
  
            retries_left -= 1
            if retries_left <= 0:
              # We are going to give up on this entire connection...
              if seeder:
                # If this is the queue seeder, we must post a stop request.
                q.put(None)
              raise
            fail = Failure()
            status = failure_to_status(fail)
            self.reportStatus(**status)
            logger.warning('Failed to process queue, will retry after %s secs: %s',
                           backoff, fail.getErrorMessage())
            next_backoff = min(backoff * 2, MAX_BACKOFF) # magic number
            conductor.reactor.callLater(backoff,
                                        consume_connection_queue,
                                        q, def_done, retries_left, next_backoff)
            return
          except Exception:
            if not conductor.reactor.running:
              break
            # some other bizarre error - just skip this batch and continue
            self.reportStatus(**failure_to_status(Failure()))
            log_exception('failed to process an IMAP query request for account %r',
                          self.details.get('id',''))
            # This was the queue seeding request; looping again to fetch a
            # queue item is likely to hang (as nothing else is in there)
            # There is no point doing a retry; this doesn't seem to be
            # connection or server related; so post a stop request and bail.
            if seeder:
              q.put(None)
              raise
      finally:
        # checking conn.tags is the only reliable way I can see to detect
        # premature disconnection inside twisted etc...
        if conn is not None and conn.tags is not None:
          # should be no need to ever 'close' - we can re-select new mailboxes
          # or just disconnect with logout...
          try:
            _ = yield conn.logout()
          except error.ConnectionLost:
            # *sometimes* we get a connection lost exception trying this.
            # Is it possible gmail just aborts the connection?
            logger.debug("ignoring ConnectionLost exception when logging out")
          except Exception:
            log_exception('failed to logout from the connection')
          # and we are done (we've probably already lost the connection whe
          # doing the logout, but better safe than sorry...)
          try:
            _ = yield defer.maybeDeferred(conn.transport.loseConnection)
          except Exception:
            log_exception("failed to drop the connection")

    @defer.inlineCallbacks
    def start_queryers(n):
      ds = []
      for i in range(n):
        d = defer.Deferred()
        consume_connection_queue(prov.query_queue, d)
        ds.append(d)
      _ = yield defer.DeferredList(ds)
      # queryers done - post to the fetch queue telling it everything is done.
      acid = self.details.get('id','')
      logger.info('%r imap querying complete - waiting for fetch queue',
                  acid)
      prov.fetch_queue.put(None)

    @defer.inlineCallbacks
    def start_fetchers(n):
      ds = []
      for i in range(n):
        d = defer.Deferred()
        consume_connection_queue(prov.fetch_queue, d)
        ds.append(d)
      _ = yield defer.DeferredList(ds)
      # fetchers done - write the cache docs last.
      if prov.updated_folder_infos:
        _ = yield prov.write_items(prov.updated_folder_infos)

    @defer.inlineCallbacks
    def start_producing(conn):
      _ = yield prov._reqList(conn)

    def log_status():
      nf = sum(len(i[2][1]) for i in prov.fetch_queue.pending if i is not None)
      if nf:
        logger.info('%r fetch queue has %d messages',
                    self.details.get('id',''), nf)

    lc = task.LoopingCall(log_status)
    lc.start(10)
    # put something in the fetch queue to fire things off, noting that
    # this is the 'queue seeder' - it *must* succeed so it writes a None to
    # the end of the queue so the queue stops.  The retry semantics of the
    # queue mean that we can't simply post the None in the finally of the
    # function - it may be called multiple times.  If the queue consumer
    # gives up on a seeder function, it posts the None for us.
    prov.query_queue.put((True, start_producing, ()))

    # fire off the producer and queue consumers.
    _ = yield defer.DeferredList([start_queryers(NUM_QUERYERS),
                                  start_fetchers(NUM_FETCHERS)])

    lc.stop()

  def get_identities(self):
    addresses = self.details.get('addresses')
    if not addresses:
      username = self.details.get('username')
      if '@' not in username:
        logger.warning(
          "IMAP account '%s' specifies a username that isn't an email address.\n"
          "This account should have an 'addresses=' entry added to the config\n"
          "file with a list of email addresses to be used with this account\n"
          "or raindrop will not be able to detect items sent by you.",
          self.details['id'])
        ret = []
      else:
        ret = [['email', username]]
    else:
      ret = [['email', addy] for addy in re.split("[ ,]", addresses) if addy]
    logger.debug("email identities for %r: %s", self.details['id'], ret)
    return ret
