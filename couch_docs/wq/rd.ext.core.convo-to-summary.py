# We take as input an 'rd.msg.conversation' schema (for a *message*) and
# emit an 'rd.conv.summary' schema for a *conversation* (ie, for a different
# rd_key than our input document).
# Building the summary is expensive, but it means less work by the front end.
# We also record we are dependent on any of the messages themselves changing,
# so when as item is marked as 'unread', the summary changes accordingly.
import itertools

# these are the schemas we need.
src_msg_schemas = [
    'rd.msg.body',
    'rd.msg.conversation',
    'rd.msg.archived',
    'rd.msg.deleted',
    'rd.msg.seen',
    'rd.msg.grouping-tag',
]

def handler(doc):
    conv_id = doc['conversation_id']
    # query to determine all messages in this convo.
    result = open_view(key=['rd.msg.conversation', 'conversation_id', conv_id],
                       reduce=False)
    # the list of items we want.
    wanted = []
    for row in result['rows']:
        msg_key = row['value']['rd_key']
        for sch_id in src_msg_schemas:
            wanted.append((msg_key, sch_id))

    infos = [item for item in open_schemas(wanted) if item is not None]
    all_msgs = []
    # turn them into a nested dictionary.
    for msg_key, item_gen in itertools.groupby(infos, lambda info: info['rd_key']):
        new = {}
        for item in item_gen:
            if item is not None:
                new[item['rd_schema_id']] = item
        all_msgs.append(new)

    identities = set()
    from_display = []
    from_display_map = {}
    good_msgs = []
    unread = []
    latest_by_recip_target = {}
    earliest_timestamp = latest_timestamp = None
    subject = None
    groups_with_unread = set()
    for msg_info in all_msgs:
        if 'rd.msg.body' not in msg_info:
            continue
        if 'rd.msg.deleted' in msg_info and msg_info['rd.msg.deleted']['deleted']:
            continue
        if 'rd.msg.archived' in msg_info and msg_info['rd.msg.archived']['archived']:
            continue
        # the message is good!
        good_msgs.append(msg_info)
        if 'rd.msg.seen' not in msg_info or not msg_info['rd.msg.seen']['seen']:
            unread.append(msg_info)
            try:
                grouping_tag = msg_info['rd.msg.grouping-tag']['tag']
            except KeyError:
                grouping_tag = None
            groups_with_unread.add(grouping_tag)
        body_schema = msg_info['rd.msg.body']            
        for field in ["to", "cc"]:
            if field in body_schema:
                for val in body_schema[field]:
                    identities.add(tuple(val))
        try:
            identities.add(tuple(body_schema['from']))
        except KeyError:
            pass # things like RSS feeds don't have a 'from'
        try:
            fd = body_schema['from_display']
            if (not fd in from_display_map):
                from_display_map[fd] = 1
                from_display.append(fd)
        except KeyError:
            pass # things like RSS feeds don't have a 'from_display'
        if earliest_timestamp is None or \
           msg_info['rd.msg.body']['timestamp'] < earliest_timestamp:
            earliest_timestamp = msg_info['rd.msg.body']['timestamp']
        if latest_timestamp is None or \
           msg_info['rd.msg.body']['timestamp'] > latest_timestamp:
            latest_timestamp = msg_info['rd.msg.body']['timestamp']
        if 'rd.msg.grouping-tag' in msg_info:
            this_group = msg_info['rd.msg.grouping-tag']['tag']
            this_ts = msg_info['rd.msg.body']['timestamp']
            cur_latest = latest_by_recip_target.get(this_group, 0)
            if this_ts > cur_latest:
                latest_by_recip_target[this_group] = this_ts
        #We want the subject from the first (topic) message
        #XXX This method doesn't guarantee the topic message but works better
        if earliest_timestamp and earliest_timestamp == msg_info['rd.msg.body']['timestamp']:
            subject = msg_info['rd.msg.body'].get('subject')

    # build a map of grouping-tag to group ID
    latest_by_grouping = {}
    keys = [["rd.grouping.info", "grouping_tags", gt]
            for gt in latest_by_recip_target]
    result = open_view(keys=keys, reduce=False)
    for row in result['rows']:
        gtag = row['key'][-1]
        gkey = row['value']['rd_key']
        this_ts = latest_by_recip_target[gtag]
        cur_latest = latest_by_grouping.get(hashable_key(gkey), 0)
        if this_ts > cur_latest:
            latest_by_grouping[hashable_key(gkey)] = this_ts

    # sort the messages and select the 3 most-recent.
    good_msgs.sort(key=lambda item: item['rd.msg.body']['timestamp'],
                   reverse=True)
    recent = good_msgs[:3]

    item = {
        'subject': subject,
        # msg list is only the "good" messages.
        'message_ids': [i['rd.msg.body']['rd_key'] for i in good_msgs],
        'unread_ids': [i['rd.msg.body']['rd_key'] for i in unread],
        'identities': sorted(list(identities)),
        'from_display': from_display,
        'earliest_timestamp': earliest_timestamp,
        'latest_timestamp': latest_timestamp,
        'grouping-timestamp': [],
        'groups_with_unread': sorted(groups_with_unread),
    }
    for target, timestamp in sorted(latest_by_grouping.iteritems()):
        item['grouping-timestamp'].append([target, timestamp])
    # our 'dependencies' are *all* messages, not just the "good" ones.
    deps = []
    for msg in all_msgs:
        # get the rd_key from any schema which exists
        msg_key = list(msg.values())[0]['rd_key']
        for sid in src_msg_schemas:
            deps.append((msg_key, sid))
    emit_schema('rd.conv.summary', item, rd_key=conv_id, deps=deps)
