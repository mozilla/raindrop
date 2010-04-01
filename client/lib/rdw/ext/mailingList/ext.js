/* ***** BEGIN LICENSE BLOCK *****
 * Version: MPL 1.1
 *
 * The contents of this file are subject to the Mozilla Public License Version
 * 1.1 (the "License"); you may not use this file except in compliance with
 * the License. You may obtain a copy of the License at
 * http://www.mozilla.org/MPL/
 *
 * Software distributed under the License is distributed on an "AS IS" basis,
 * WITHOUT WARRANTY OF ANY KIND, either express or implied. See the License
 * for the specific language governing rights and limitations under the
 * License.
 *
 * The Original Code is Raindrop.
 *
 * The Initial Developer of the Original Code is
 * Mozilla Messaging, Inc..
 * Portions created by the Initial Developer are Copyright (C) 2009
 * the Initial Developer. All Rights Reserved.
 *
 * Contributor(s):
 * */

/*jslint plusplus: false, nomen: false */
/*global require: false */
"use strict";


require.modify("rd/conversation", "rdw/ext/mailingList/ext-rd/conversation",
    ["rd", "rd/api", "rd/conversation"],
    function (rd, api) {
        //Allow a "mailingList" method on the rd.conversation data API.
        rd.applyExtension("rdw/ext/mailingList/ext", "rd/conversation", {
            add: {
                /**
                 * Gets the most recent mailing list messages up to limit, then pulls
                 * the conversations associated with those messages. Conversation with
                 * the most recent message will be first.
                 * @param {String} listId
                 * @param {String} limit
                 * @param {Function} callback
                 * @param {Function} [errback]
                 */
                mailingList: function (listId, limit, skip, callback, errback) {
                    api().megaview({
                        key: ["rd.msg.email.mailing-list", "list_id", listId],
                        reduce: false,
                        limit: limit,
                        skip: skip
                    })
                    .ok(this, function (json) {
                        //Get message keys
                        var keys = [], i, row;
                        for (i = 0; (row = json.rows[i]); i++) {
                            keys.push(row.value.rd_key);
                        }
        
                        this.messageKey(keys, callback, errback);
                    });
                }
            }
        });
    }
);

require.modify("rd/MegaviewStore", "rdw/ext/mailingList/ext-rd/MegaviewStore",
    ["rd", "dojo", "rd/api", "rd/MegaviewStore"],
    function (rd, dojo, api) {
        //Allow mailingList queries via the rd.MegaviewStore dojo.data store.
        rd.applyExtension("rdw/ext/mailingList/ext", "rd/MegaviewStore", {
            addToPrototype: {
                schemaQueryTypes: [
                    "mailingList"
                ],
    
                /**
                 * Does a mailingList query for the "mailingList" schemaQueryType.
                 * @param {String} query
                 * @param {Number} count
                 */
                mailingListQuery: function (query, count) {
                    var dfd = new dojo.Deferred(),
                        args = {
                        startkey: ["rd.mailing-list", "id", query],
                        endkey: ["rd.mailing-list", "id", query + "\u9999"],
                        reduce: false,
                        ioPublish: false
                    };
    
                    if (count && count !== Infinity) {
                        args.limit = count;
                    }
    
                    api().megaview(args)
                    .ok(this, function (json) {
                        var items = [], i, row, name;
                        for (i = 0; (row = json.rows[i]); i++) {
                            name = row.key[2];
                            if (!name) {
                                continue;
                            }
                            items.push({
                                id: row.value.rd_key[1],
                                type: "mailingList",
                                name: row.key[2]
                            });
                        }
                        this._addItems(items);
                        dfd.callback();
                    })
                    .error(dfd);
                    return dfd;
                }
            }
        });
    }
);

require.modify("rdw/DataSelector", "rdw/ext/mailingList/ext-rdw/DataSelector",
    ["rd", "i18n!rdw/ext/mailingList/nls/i18n", "rdw/DataSelector"],
    function (rd, i18n) {
        //Allow DataSelector to use mailingList in the all selector, and to
        //handle mailingList selections.
        rd.applyExtension("rdw/ext/mailingList/ext", "rdw/DataSelector", {
            addToPrototype: {
                allType: [
                    "mailingList"
                ],

                typeLabels: {
                    "mailingList": i18n.mailingListTypeLabel
                },

                /**
                 * Dispatch function when a mailingList is selected.
                 * @param {String} list
                 */
                mailingListSelected: function (list) {
                    rd.setFragId("rd:mailingList:" + list);    
                }
            }
        });
    }
);

require.modify("rdw/Organizer", "rdw/ext/mailingList/ext-rdw/Organizer",
    ["rd", "dojo", "rd/tag", "rdw/Organizer"],
    function (rd, dojo, tag) {
        //Apply a modification to the Organizer to show mailing lists.
        rd.applyExtension("rdw/ext/mailingList/ext", "rdw/Organizer", {
            addToPrototype: {
                listOrder: [
                    "listMailingList"
                ],
    
                /** Shows a list of mailing lists available for viewing.*/
                listMailingList: function () {
                    tag.lists(dojo.hitch(this, function (ids) {
                        var html = "", i, id;
                        for (i = 0; (id = ids[i]); i++) {
                            html += rd.template('<option value="rd:mailingList:${id}">${name}</option>', {
                                id: id,
                                //TODO: use the mailing list doc's "name" property if available.
                                name: id.split(".")[0]
                            });
                        }
        
                        if (html) {
                            this.addItems("listMailingList", "Mailing Lists", dojo._toDom(html));
        
                            //Listen to set current selection state.
                            this.subscribeSelection("mailingList");
                        }
                    }));
                }
            }
        });
    }
);

require.modify("rdw/Summary", "rdw/ext/mailingList/ext-rdw/Summary",
    ["rd", "dojo", "rdw/Summary", "rdw/ext/mailingList/Summary"],
    function (rd, dojo, Summary, MlSummary) {
        //Modify rdw.Summary to allow showing a summary
        //for mailing lists.
        rd.applyExtension("rdw/ext/mailingList/ext", "rdw/Summary", {
            addToPrototype: {
                /**
                 * Responds to rd-protocol-mailingList topic.
                 * @param {String} listId
                 */
                mailingList: function (listId) {
                    this.addSupporting(new MlSummary({
                            listId: listId
                        }, dojo.create("div", null, this.domNode)));
                }
            }
        });
    }
);

require.modify("rdw/SummaryGroup", "rdw/ext/mailingList/ext-rdw/SummaryGroup",
    ["rd", "dojo", "rdw/SummaryGroup", "rdw/ext/mailingList/SummaryGroup"],
    function (rd, dojo, SummaryGroup, MlSummaryGroup) {
        //Modify rdw.SummaryGroup to allow showing a summary
        //for mailing lists.
        rd.applyExtension("rdw/ext/mailingList/ext", "rdw/SummaryGroup", {
            addToPrototype: {
                topics: {
                    "rd-protocol-mailingList": "mailingList"
                },
      
                /**
                 * Responds to rd-protocol-mailingList topic.
                 * @param {String} listId
                 */
                mailingList: function (listId) {
                    this.addSupporting(new MlSummaryGroup({
                            listId: listId
                        }, dojo.create("div", null, this.domNode)));
                }
            }
        });
    }
);

require.modify("rdw/Conversations", "rdw/ext/mailingList/ext-rdw/Conversations",
    ["rd", "dojo", "rd/conversation", "rdw/Conversations"],
    function (rd, dojo, conversation) {
        //Modify rdw.Conversations to allow loading mailing lists.
        rd.applyExtension("rdw/ext/mailingList/ext", "rdw/Conversations", {
            addToPrototype: {
                topics: {
                    "rd-protocol-mailingList": "mailingList"
                },
    
                /**
                 * Responds to rd-protocol-mailingList topic.
                 * @param {String} listId
                 */
                mailingList: function (callType, listId) {
                    conversation.mailingList(listId, this.conversationLimit, this.skipCount, dojo.hitch(this, function (conversations) {    
                        this.updateConversations(callType, "summary", conversations);

                        //Only set up summary widget if this is a fresh call
                        //to the twitter timeline.
                        if (!callType) {
                            if (this.summaryWidget.mailingList) {
                                this.summaryWidget.mailingList(listId);
                            }
                        }
                    }));
                }
            }
        });
    }
);

require.modify("rdw/Widgets", "rdw/ext/mailingList/ext-rdw/Widgets",
    ["rd", "dijit", "rdw/Widgets", "rdw/ext/mailingList/Group"],
    function (rd, dijit) {
        //Modify rdw.Widgets to allow showing mailing lists.
        rd.applyExtension("rdw/ext/mailingList/ext", "rdw/Widgets", {
            addToPrototype: {
                summaryModules: [
                    "rdw/ext/mailingList/Group"
                ]
            }
        });
    }
);

