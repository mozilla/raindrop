# ***** BEGIN LICENSE BLOCK *****
# * Version: MPL 1.1
# *
# * The contents of this file are subject to the Mozilla Public License Version
# * 1.1 (the "License"); you may not use this file except in compliance with
# * the License. You may obtain a copy of the License at
# * http://www.mozilla.org/MPL/
# *
# * Software distributed under the License is distributed on an "AS IS" basis,
# * WITHOUT WARRANTY OF ANY KIND, either express or implied. See the License
# * for the specific language governing rights and limitations under the
# * License.
# *
# * The Original Code is Raindrop.
# *
# * The Initial Developer of the Original Code is
# * Mozilla Messaging, Inc..
# * Portions created by the Initial Developer are Copyright (C) 2009
# * the Initial Developer. All Rights Reserved.
# *
# * Contributor(s):
# *

# A very hacky script to try and get some "benchmarks" for raindrop's backend.
# The intent is that this script can help you determine the relative
# performance cost or benefit of particular strategies.

from __future__ import division

import os
import time
import optparse
try:
    import simplejson as json
except ImportError:
    import json

from twisted.internet import reactor, defer
from twisted.python import log

from raindrop.tests import TestCaseWithCorpus, FakeOptions
from raindrop.config import get_config

def format_num_bytes(nbytes):
    KB = 1000
    MB = KB * 1000
    GB = MB * 1000
    for (what, desc) in ((GB, 'GB'), (MB, 'MB'), (KB, 'KB')):
        if nbytes > what:
            return "%.3g%s" % ((nbytes/what), desc)
    return "%sB" % nbytes

class CorpusHelper(TestCaseWithCorpus):
    def __init__(self, opts):
        self.opt_dict = opts
    def get_options(self):
        opts = FakeOptions()
        for name, val in self.opt_dict.iteritems():
            setattr(opts, name, val)
        return opts

    # A helper for the enron corpos - nothing specific to enron really - just
    # a collection of files on disk, each one being a single rfc822 message
    def gen_enron_items(self, path):
        for root, dirs, files in os.walk(path):
            this = []
            for file in files:
                fq = os.path.join(root, file)
                this.append(self.rfc822_to_schema_item(open(fq, "rb")))
            if this:
                yield this

    @defer.inlineCallbacks
    def load_enron_messages(self, path):
        _ = yield self.init_corpus('enron')
        num = 0
        for items in self.gen_enron_items(path):
            num += len(items)
            _ = yield self.doc_model.create_schema_items(items)
        defer.returnValue(num)

    
@defer.inlineCallbacks
def load_corpus(testcase, opts):
    if opts.enron_dir:
        num = yield testcase.load_enron_messages(opts.enron_dir)
    else:
        # for now, just use the hand-rolled corpus
        num = yield testcase.load_corpus('hand-rolled')
    defer.returnValue(num)


@defer.inlineCallbacks
def load_and_sync(testcase, opts):
    num = yield load_corpus(testcase, opts)
    _ = yield testcase.ensure_pipeline_complete()
    defer.returnValue(num)


@defer.inlineCallbacks
def timeit(func, *args):
    start = time.clock()
    ret = yield defer.maybeDeferred(func, *args)
    took = time.clock()-start
    defer.returnValue((ret, took))

@defer.inlineCallbacks
def report_db_state(db, opts):
    info = yield db.infoDB()
    print "DB has %(doc_count)d docs at seq %(update_seq)d in %(disk_size)d bytes" % info
    if opts.couch_dir:
        # report what we find on disk about couch.
        dbname = 'raindrop_test_suite' # hardcoded by test-suite helpers we abuse.
        dbsize = os.path.getsize(os.path.join(opts.couch_dir, dbname + ".couch"))
        # and walk looking for view files.
        vsize = 0
        for root, dirs, files in os.walk(os.path.join(opts.couch_dir, ".%s_design" % dbname)):
            vsize += sum(os.path.getsize(os.path.join(root, name)) for name in files)
        ratio = vsize / dbsize
        nb = format_num_bytes
        print "DB on disk is %s, views are %s (%s total, ratio 1:%0.2g)" % \
             (nb(dbsize), nb(vsize), nb(dbsize+vsize), ratio)

@defer.inlineCallbacks
def run_timings_async(_, opts):
    print "Starting asyncronous loading and processing..."
    tc = CorpusHelper({'no_process': True})
    _ = yield tc.setUp()
    ndocs, avg = yield timeit(load_corpus, tc, opts)
    print "Loaded %d documents in %.3f" % (ndocs, avg)
    # now do a 'process' on one single extension.
    tc.pipeline.options.exts = ['rd.ext.core.msg-rfc-to-email']
    _, avg = yield timeit(tc.pipeline.start_backlog)
    print "Ran 1 extension in %.3f" % (avg)
    # now do a few in (hopefully) parallel
    tc.pipeline.options.exts = ['rd.ext.core.msg-email-to-body',
                                'rd.ext.core.msg-email-to-mailinglist',
                                'rd.ext.core.msg-email-to-grouping-tag',
                                'rd.ext.core.msg-body-to-quoted',
                                'rd.ext.core.msg-body-quoted-to-hyperlink',
                                ]
    _, avg = yield timeit(tc.pipeline.start_backlog)
    print "Ran %d extensions in %.3f" % (len(tc.pipeline.options.exts), avg)
    # now the 'rest'
    tc.pipeline.options.exts = None
    _, avg = yield timeit(tc.pipeline.start_backlog)
    print "Ran remaining extensions in %.3f" % (avg,)
    _ = yield report_db_state(tc.pipeline.doc_model.db, opts)
    # try unprocess then process_backlog
    _, avg = yield timeit(tc.pipeline.unprocess)
    print "Unprocessed in %.3f" % (avg,)
    _, avg = yield timeit(tc.pipeline.start_backlog)
    print "re-processed in %.3f" % (avg,)
    _ = yield report_db_state(tc.pipeline.doc_model.db, opts)


@defer.inlineCallbacks
def run_timings_sync(_, opts):
    print "Starting syncronous loading..."
    tc = CorpusHelper({})
    _ = yield tc.setUp()
    ndocs, avg = yield timeit(load_and_sync, tc, opts)
    print "Loaded and processed %d documents in %.3f" % (ndocs, avg)
    _ = yield report_db_state(tc.pipeline.doc_model.db, opts)


@defer.inlineCallbacks
def run_api_timings(_, opts):
    import httplib
    from urllib import urlencode    
    couch = get_config().couches['local']
    c = httplib.HTTPConnection(couch['host'], couch['port'])
    tpath = '/%s/_api/inflow/%s'
    
    def make_req(path):
        c.request('GET', path)
        return c.getresponse()

    @defer.inlineCallbacks
    def do_timings(api, desc=None, **kw):
        api_path = tpath % (couch['name'], api)
        if kw:
            opts = kw.copy()
            for opt_name in opts:
                opts[opt_name] = json.dumps(opts[opt_name])
            api_path += "?" + urlencode(opts)
        resp, reqt = yield timeit(make_req, api_path)
        dat, respt = yield timeit(resp.read)
        if not desc:
            desc = api
        if resp.status != 200:
            print "*** api %r failed with %s: %s" % (desc, resp.status, resp.reason)
        print "Made '%s' API request in %.3f, read response in %.3f (size was %s)" \
              % (desc, reqt, respt, format_num_bytes(len(dat)))
        defer.returnValue(json.loads(dat))

    result = yield do_timings("grouping/summary")
    for gs in result:
        title = gs.get('title') or gs['rd_key']
        _ = yield do_timings("conversations/in_groups", "in_groups: " + str(title),
                             limit=60, message_limit=2, keys=[gs['rd_key']])


def main():
    parser = optparse.OptionParser()
    parser.add_option("", "--enron-dir",
                      help=
"""Directory root of an enron-style corpus to use.  You almost certainly do
not want to specify the root of the enron corpus - specify one of the
child (leaf) directories.  For example {root}/beck-s/eurpoe holds 166
documents.""")
    parser.add_option("", "--couch-dir",
                      help=
"""Directory where the couchdb database files are stored.  If specified
the size on disk of the DB and views will be reported.""")
    parser.add_option("", "--skip-sync", action="store_true",
                      help="don't benchmark sync processing")
    parser.add_option("", "--skip-async", action="store_true",
                      help="don't benchmark async processing")
    parser.add_option("", "--skip-api", action="store_true",
                      help="don't benchmark api processing")
    opts, args = parser.parse_args()

    d = defer.Deferred()
    if not opts.skip_async:
        d.addCallback(run_timings_async, opts)
    if not opts.skip_sync:
        d.addCallback(run_timings_sync, opts)
    if not opts.skip_api:
        d.addCallback(run_api_timings, opts)

    def done(whateva):
        reactor.stop()

    d.addCallbacks(done, log.err)
    
    reactor.callWhenRunning(d.callback, None)
    reactor.run()


if __name__ == "__main__":
    main()
