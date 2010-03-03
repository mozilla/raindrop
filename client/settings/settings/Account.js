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
/*global require: false, alert: false, setTimeout: false */
"use strict";

require.def("settings/Account",
["require", "dojo", "rd", "rd/api", "dijit/_Widget", "dijit/_Templated",
 "text!settings/AccountSimple!html"],
function (require,   dojo,   rd,   api,      Widget,          Templated, template) {

    return dojo.declare("settings.Account", [Widget, Templated], {
        //The couchdb doc.
        doc: null,

        //HTML template for simple cases.
        simpleAccount: template,

        kindProps: {
            gmail: {
                _name: "Gmail",
                _domain: "mail.google.com/",
                _userNameSuffix: "@gmail.com",
                proto: "imap",
                port: 993,
                ssl: true
            },
            twitter: {
                _name: "Twitter",
                _domain: "twitter.com/",
                _userNameSuffix: "",
                proto: "twitter"
            }
        },

        commonDocProps: {
            username: "",
            name: "",
            password: "",
            rd_megaview_expandable: "identities",
            rd_schema_id: "rd.account"
        },

        postMixInProperties: function () {
            this.inherited("postMixInProperties", arguments);
    
            this.kindDocProps = this.kindProps[this.doc.kind];
    
            if (!this.doc._id) {
                this.doc = this.generateDoc(this.doc);
            }
    
            if (!this.doc.password) {
                this.doc.password = "";
            }
    
            //Choose the right template. For now, just always use simple account.
            this.templateString = this.simpleAccount;
        },
    
        generateDoc: function (doc) {
            //fills in missing doc info so a proper save can be done.
            dojo.mixin(doc, this.commonDocProps);
            
            //Skip properties with underscores, those are used for non-couch purposes.
            for (var prop in this.kindDocProps) {
                if (prop.indexOf("_") !== 0) {
                    doc[prop] = this.kindDocProps[prop];
                }
            }
    
            return doc;
        },
    
        onSave: function (evt) {
            //If username has changed, then delete the old document and generate
            //a new doc?
            dojo.stopEvent(evt);
    
            var userName = dojo.trim(this.userNameNode.value || ""),
                password = this.passwordNode.value;
            if (userName) {
                this.doc.password = password;
                this.doc.name = userName;
                if (!this.doc.id) {
                    this.doc.id = this.doc.name + "-" + this.doc.kind;
                }
                this.doc.username = userName + this.kindDocProps._userNameSuffix;
                if (!this.doc.rd_key) {
                    this.doc.rd_key = [
                        "raindrop-account",
                        "account!" + this.doc.id
                    ];
                }
    
                this.doc.identities = [
                    [(this.doc.proto === "imap" ? "email" : this.doc.proto), this.doc.username]
                ];
    
                rd.api().put({
                    doc: this.doc
                })
                .ok(this, function (doc) {
                    //Update the rev.
                    this.doc._rev = doc._rev;
                    
                    if (this.doc.kind === "gmail") {
                        //Need to create an smtp record too.
                        //TODO: make this extensible.
                        //First see if there is an existing record
                        //and delete it.
                        rd.api().megaview({
                            key: ["rd.account", "proto", "smtp"],
                            reduce: false,
                            include_docs: true
                        })
                        .ok(this, function (json) {
                            if (json.rows.length) {
                                rd.api().deleteDoc({
                                    doc: json.rows[0].doc
                                })
                                .ok(this, function () {
                                    this.saveSmtpDoc();
                                });
                            } else {
                                this.saveSmtpDoc();
                            }
                        });
                    } else {
                        this.accountSaved();
                    }
                })
                .error(this, "onError");
            }
        },
    
        saveSmtpDoc: function () {
            rd.api().put({
                doc: {
                    host: "smtp.gmail.com",
                    id: "smtp",
                    identities: [
                        ["email", this.doc.username]
                    ],
                    name: "smtp",
                    port: 587,
                    proto: "smtp",
                    rd_key: [
                        "raindrop-account",
                        "account!smtp"
                    ],
                    rd_megaview_expandable: [
                        "identities"
                    ],
                    rd_schema_id: "rd.account",
                    rd_source: null,
                    ssl: false,
                    username: this.doc.username,
                    password: this.doc.password
                }
            })
            .ok(this, function () {
                this.accountSaved();
            });
        },
    
        onDelete: function (evt) {
            dojo.stopEvent(evt);
    
            if (this.doc._id) {
                rd.api().deleteDoc({
                    doc: this.doc
                })
                .ok(this, function () {
                    this.userNameNode.value = "";
                    this.passwordNode.value = "";
                    this.userNameDisplayNode.innerHTML = "";
                    this.showMessage("Account Deleted");
                });
            }
        },
    
        onError: function (err) {
            alert(err);
        },
        
        accountSaved: function () {
            this.showMessage("Account Saved");
            rd.escapeHtml(this.doc.name, this.userNameDisplayNode, "only");
        },
    
        showMessage: function (message) {
            rd.escapeHtml(message, this.messageNode, "only");
            setTimeout(dojo.hitch(this, function () {
                this.messageNode.innerHTML = "&nbsp;";
            }), 5000);
        }
    });
});
