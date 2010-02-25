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

require.def("rd/api/identity",
["rd", "dojo", "rd/api"],
function (rd, dojo, api) {
    var identity = {
        /**
         * maps each identity ID to a contact ID, a sort of cache for
         * rd.identity.contacts documents.
         */
        _contactMap: {},
    
        _byIdty: {},
        _byContact: {},
    
        /**
         * @private
         * filters the found from the missing identities so only the
         * missing ones are fetched via the network.
         *
         * @param {Array} ids an array of identity IDs, where each
         * identity ID is itself an array of [identityType, identityName].
         *
         * @returns {Object} has two properties, "found" and "missing"
         * that are arrays of identity IDs.
         */
        _filter: function (ids) {
            var missing = [], found = [], i, id, temp;
            for (i = 0; (id = ids[i]); i++) {
                temp = this._idty(id);
                (temp ? found.push(temp) : missing.push(id));
            }

            return {
                found: found.length ? found : null,
                missing: missing.length ? missing: null            
            };
        },

        _filterByContactMap: function (ids) {
            
        },
    
        /**
         * @private
         * fetches an identity based on an identity ID, but only if attached
         * to a contact.
         *
         * @param {dojo.Deferred} dfd The deferred that should be called
         * with the results.
         *
         * @param {Object} args arguments to pass to the couch calls.
         * 
         * @param {Array} ids an array of identity IDs, where each
         * identity ID is itself an array of [identityType, identityName].
         */
        _contactIdentity: function (dfd, args, ids) {
            //Make sure the rd.identity.contacts records have been fetched.
            this._fetchIdentityContacts().addErrback(dfd, "errback").addCallback(this, function () {
                var found = [], i, id;
                for (i = 0; (id = ids[i]); i++) {
                    if (this._byIdty[id.join(",")]) {
                        found.push(id);
                    }
                }
    
                //Fetch the identity docs for the found ones, if there are any.
                if (!found.length) {
                    dfd.callback([]);
                } else {
                    api().identity({
                        ids: found
                    })
                    .ok(this, function (identities) {
                        dfd.callback(identities);
                    })
                    .error(dfd);
                }
            });
        },
    
        /**
         * @private
         * fetches all rd.identity.contacts records, since it is hard to do mapping
         * from a contact back to identities without lots of queries. Also makes the
         * _contactIdentity work simpler.
         */
        _fetchIdentityContacts: function () {
            if (this._idtyContactsDfd) {
                return this._idtyContactsDfd;
            }
    
            this._idtyContactsDfd = new dojo.Deferred();
    
            api().megaview({
                key: ["rd.core.content", "schema_id", "rd.identity.contacts"],
                reduce: false,
                include_docs: true
            })
            .ok(this, function (json) {
                //Cycle through each document. The rd_key is the full identity ID, we just
                //need the second part of it. It has an array of contacts but each item
                //in the contacts array has other info about the contact, we just need
                //the first part, the contactId.
                var i, j, row, doc, idty, idKey, byIdty, contact, contactId, byContact;
                for (i = 0; (row = json.rows[i]) && (doc = row.doc); i++) {
                    idty = doc.rd_key[1];
                    idKey = idty.join(",");
                    byIdty = this._byIdty[idKey] || (this._byIdty[idKey] = []);
                    for (j = 0; (contact = doc.contacts[j]); j++) {
                        contactId = contact[0];
                        byIdty.push(contactId);
                        byContact = this._byContact[contactId] || (this._byContact[contactId] = []);
                        byContact.push(idty);
                    }
                }
                this._idtyContactsDfd.callback();
            }).error(this._idtyContactsDfd);
            return this._idtyContactsDfd;
        },
    
        /**
         * @private
         * fetches an identity based on an identity ID.
         * Tries to use couch info, but for certain services
         * falls back to using the service API associated with the identity.
         *
         * @param {dojo.Deferred} dfd The deferred that should be called
         * with the results.
         *
         * @param {Object} args arguments to pass to the couch calls.
         * 
         * @param {Array} ids an array of identity IDs, where each
         * identity ID is itself an array of [identityType, identityName].
         */
        _identity: function (dfd, args, ids) {
            //Figure out if we have all the identities we need.
            ids = this._filter(ids);
    
            if (!ids.missing) {
                dfd.callback(ids.found || []);
            } else {
                var found = ids.found || [];
    
                //Wait for rd.identity.contacts records to load, so that we can know
                //what identities to not even to try to fetch since they will not be
                //there. TODO: this may be a bad assumption, but the other option is
                //a lot of requests to the couch for non-existent identities.
                this._fetchIdentityContacts().addErrback(dfd, "errback").addCallback(this, function () {
                    //Build a list of keys to use for megaview call.
                    var keys = [], unknowns = [], map = {}, i, id;
                    for (i = 0; (id = ids.missing[i]); i++) {
                        if (!this._byIdty[id.join(",")]) {
                            //The rd.identity record will not exist, create a fake one.
                            found.push(this._createFakeIdentity(id));
                        } else {
                            keys.push(["rd.core.content", "key-schema_id", [["identity", id], "rd.identity"]]);
                            map[id.join(",")] = 0;
                        }
                    }

                    if (!keys.length) {
                        dfd.callback(found);
                    } else {
                        api().megaview({
                            keys: keys,
                            reduce: false,
                            include_docs: true
                        })
                        .ok(this, function (json) {
                            var i, row, doc, empty = {}, prop;
                            for (i = 0; (row = json.rows[i]) && (doc = row.doc); i++) {
                                //Store for future calls.
                                doc = this._storeIdty(doc);
    
                                //Mark it found
                                map[doc.rd_key[1].join(",")] = 1;
    
                                //Add to result set.
                                found.push(doc);
                            }
    
                            //For all the ids not found, create fake identities.
                            for (prop in map) {
                                if (!(prop in empty) && !map[prop]) {
                                    found.push(this._createFakeIdentity(prop.split(",")));
                                }
                            }
    
                            //All done
                            dfd.callback(found);
                        })
                        .error(dfd);
                    }
                });
            }
        },
    
        /**
         * creates a fake identity and puts it in the store.
         *
         * @param {Array} id the identity ID to use for the fake record.
         *
         * @returns {Object} a fake identity object, with an _isFake = true property.
         */
        _createFakeIdentity: function (id) {
            return this._storeIdty({
                // it is not clear if we should use a 'real' identity ID here?
                // theoretically all the fields being empty should be enough...
                rd_key: ['identity', id],
                rd_schema: 'rd.identity',
                //Mark this as a fake record
                _isFake: true
            });
        },
    
        /**
         * @private
         * creates an identity document for the given a mail message bag.
         * The callback will receive the identity document as the only argument.
         *
         * @param {dojo.Deferred} dfd The deferred that should be called
         * with the results.
         *
         * @param {Object} args arguments to pass to the couch calls.
         * 
         * @param {Object} msg the message object
         */
        _createEmailIdentity: function (dfd, args, msg) {
            var body = msg.schemas["rd.msg.body"],
                from = body.from[1], idty;

            //Generate the new document.
            idty = {
                rd_key: [
                    "identity",
                    ["email", from]
                ],
                rd_schema_id: "rd.identity",
                rd_source: [msg.schemas["rd.msg.email"]._id],
                items : {
                    name: body.from_display,
                    nickname: from
                }
            };
    
            //Insert the document.
            api().createSchemaItem(idty)
            .ok(this, function (idty) {
                //Update this data store.
                idty = this._storeIdty(idty);
                dfd.callback(idty);
            })
            .error(dfd);
        },

        /**
         * @private
         *
         * Gets or creates rd.identity.sender-flags schema for a given identity ID.
         * 
         * @param {dojo.Deferred} dfd The deferred that should be called
         * with the results.
         *
         * @param {Object} args arguments to pass to the couch calls.
         * 
         * @param {Array} the identity_id
         *
         * @param {Object} sourceSchema the schema that is the basis for this request.
         * Must be something with an _id and _rev properties.
         * 
         * @param {String} [flags] the flags value to use, if wanting to set
         * the schema. Leave blank to get the any existing schema
         */
        _senderFlags: function (dfd, args, id, sourceSchema, flags) {
            if (!flags) {
                //Just get the value.
                api().megaview({
                    key: ["rd.core.content", "key-schema_id", [["identity", id], "rd.identity.sender-flags"]],
                    reduce: false,
                    include_docs: true
                })
                .ok(this, function (json) {
                    var schema = null;
                    if (json.rows && json.rows.length) {
                        schema = json.rows[0].doc;
                    }
                    dfd.callback(schema);
                })
                .error(dfd);
            } else {
                //Setting the value.

                //First get the value, so we use the right _rev and such.
                args = dojo.delegate(args);
                args.flags = null;

                api().identitySenderFlags(args)
                .ok(function (schema) {
                    var newSchema = {
                        rd_key: ["identity", id],
                        rd_schema_id: "rd.identity.sender-flags"
                    };
                    for (var flagname in flags) {
                        newSchema[flagname] = flags[flagname];
                    }

                    if (schema && schema._rev) {
                        newSchema._rev = schema._rev;
                    }

                    api().put({
                        doc: newSchema
                    })
                    .ok(dfd)
                    .error(dfd);
                })
                .error(dfd);
            }
        },

        /**
         * @private
         * helper function for getting an identity from a service store.
         *
         * @param id {Array} and identity ID
         *
         * @returns {Object}
         */
        _idty: function (id) {
            return dojo.getObject(id[0], true, this)[id[1]];        
        },
    
        /**
         * @private
         * stores the identity on this object to avoid hitting the
         * database repeatedly for the same info.
         * 
         * @param {Object} idty the rd.identity schema doc for an identity.
         *
         * @returns the identity. It could have been modified from the one
         * passed in to the function.
         */
        _storeIdty: function (idty) {
            var identity_id = idty.rd_key[1],
                //Add the identity to the store for that service.
                svc = dojo.getObject(identity_id[0], true, this),
                uId = identity_id[1];
            if (!svc[uId] || svc[uId]._isFake) {
                svc[uId] = idty;
            }
    
            if (idty.image) {
                // Fix up the image URL; a leading '/' means it is a URL in our
                // couch DB.
                if (idty.image[0] === "/") {
                    idty.image = rd.dbPath + idty.image.substring(1, idty.image.length);
                }
            }
    
            return idty;
        }
    };

    api.extend({
        /**
         * @lends rd.api
         * Loads a set of identities. It will use the previous call's results,
         * or, optionally pass an args.ids which can be an array of identity IDs,
         * where each identity ID is itself an array of [identityType, identityName].
         */
        identity: function (args) {
            if (args && args.ids) {
                identity._identity(this._deferred, args, args.ids);
            } else {
                this.addParentCallback(dojo.hitch(identity, "_identity", this._deferred, args));
            }
            return this;
        },
    
        /**
         * @lends rd.api
         * Loads a set of identities but only if they are attached to a contact
         * It will use the previous call's results,
         * or, optionally pass an args.ids which can be an array of identity IDs,
         * where each identity ID is itself an array of [identityType, identityName].
         */
        contactIdentity: function (args) {
            if (args && args.ids) {
                identity._contactIdentity(this._deferred, args, args.ids);
            } else {
                this.addParentCallback(dojo.hitch(identity, "_contactIdentity", this._deferred, args));
            }
            return this;
        },
    
        /**
         * @lends rd.api
         * creates an identity document for the given a mail message bag.
         * Returns an rd.identity doc to the ok callbacks.
         *
         * @param {Object} args options for the couchdb calls
         * @param {Object} args.msg the msg object.
         */
        createEmailIdentity: function (args) {
            if (args && args.msg) {
                identity._createEmailIdentity(this._deferred, args, args.msg);
            } else {
                this._deferred.errback(new Error("rd.api().identity.createEmailIdentity " +
                                                 "requires an args.msg argument."));
            }
            return this;
        },
        
        /**
         * @lends rd.api
         * Gets or creates 'sender flags' for a given identity ID.
         * Returns the rd.identity.sender-flags schema to the ok callbacks. If
         * flags are passed in the args, then it is a set operation, otherwise
         * this call does a get for the any existing flags schema for the
         * identity.
         *
         * @param {Object} args options for the couchdb calls
         * @param {Array} args.id the identity ID to use.
         * @param {Object} args.sourceSchema the schema that is the basis for this request.
         * Must be something with an _id and _rev properties. Required for set calls.
         * @param {Object} [args.flags] the new flags.
         * Example: {bulk: true}
         */
        identitySenderFlags: function (args) {
            if (args && args.id) {
                identity._senderFlags(this._deferred, args, args.id, args.sourceSchema, args.flags);
            } else {
                this._deferred.errback(new Error("rd.api().identity.senderFlags " +
                                                 "requires an args.id argument."));
            }
            return this;
        }
    });

    return identity;
});