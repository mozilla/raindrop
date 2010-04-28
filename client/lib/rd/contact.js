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
/*global require: false, console: false */
"use strict";

require.def("rd/contact",
["rd", "dojo", "dojo/DeferredList", "couch", "rd/_api", "rd/api", "rd/api/identity"],
function (rd, dojo, DeferredList, couch, _api, api, identity) {
    //Derives from rd/_api
    var contact = dojo.delegate(_api);
    
    dojo.mixin(contact, {
        //Storage by contactId
        _store: [],
        _byContact: {},
        _byIdty: {},
        _idtyMapIds: {},
        _listStatus: "unfetched",
        _matches: {},
    
        //Returns a list of possible contact matches to the callback.
        matches: function (/*String*/name, /*Function*/callback, /*Function?*/errback) {
            //Use cache when possible.
            if (name in this._matches) {
                callback(this._matches[name]);
                return;
            }
    
            this.list(dojo.hitch(this, function (contacts) {
                var matches = [],
                    matchIds = {},
                    //Take off @ domain from an email, then separate names in original
                    //name by whitespace.
                    sourceNames = name.split("@")[0].toLowerCase(),
                    k, from, i, contact, names, contactId, j, identity;

                sourceNames = sourceNames.split(/\s+/);
    
                //Cycle through the names to try and find a match.
                //TODO: This is a bit expensive. Ideally this could become a back-end extension,
                //although there are "fresher" results if this is executed at display-time.
                //May not be worth the cost though, back-end extension might be better.
                for (k = 0; (from = sourceNames[k]); k++) {
                    for (i = 0; (contact = contacts[i]); i++) {
                        //See if contact name matches
                        names = contact.name.split(/\s+/);
                        contactId = contact.rd_key[1];
                        if (this._hasNameMatch(from, names)) {
                            if (!matchIds[contactId]) {
                                matches.push(contact);
                                matchIds[contactId] = 1;
                            }
                            continue;
                        }
    
                        //Check each identity for a match.
                        for (j = 0; (identity = contact.identities[j]); j++) {
                            names = identity.name.split(/\s+/);
                            names.unshift(identity.nickname);
                            if (this._hasNameMatch(from, names)) {
                                if (!matchIds[contactId]) {
                                    matches.push(contact);
                                    matchIds[contactId] = 1;
                                }
                                break;
                            }
                        }
                    }
                }
                this._matches[name] = matches;
                callback(matches);
            }), errback);
            
            return; //For strict JS checking.
        },
    
        //Returns a list of all contacts with their identities loaded.
        list: function (/*Function*/callback, /*Function?*/errback) {
            if (this._listStatus === "done") {
                callback(this._store);
            } else {
                this._loaded = false;
                this._listStatus = "needFetch";
                this.list.apply(this, arguments);
            }
        },
    
        //Gets a contact with its associated identities attached. Either one
        //contactId can be passed or an array of contactId strings.
        get: function (/*String|Array*/contactId, /*Function*/callback, /*Function?*/errback) {
            var contact = this._store[contactId],
                isSingle = (typeof contactId === "string"),
                contacts = this._get(contactId);
    
            if (!contacts.unknown) {
                if (!contacts.missing) {
                    callback(isSingle ? contacts.found[0] : contacts.found);
                } else {
                    this._loadIdtys(contacts.missing, function (found) {
                        if (contacts.found) {
                            found = contacts.found.concat(found);
                        }
                        callback(isSingle ? found[0] : found);
                    }, errback);
                }
            } else {
                (errback && errback(new Error("unknown identity: " + contacts.unknown)));
            }
        },
    
        //Fetches a contact via the identity. Just one identityId
        //can be passed, or multiple identitIds as an array can be passed.
        //Since an identity can be tied to more than one contact, the callback may receive
        //an array of contacts instead of just one contact, so the list
        //of contacts received may be of different length than the identityIds
        //passed to this function.
        byIdentity: function (/*Array*/identityId, /*Function*/callback, /*Function?*/errback) {
            if (typeof identityId[0] === "string") {
                identityId = [identityId];
            }
    
            var contactIds = [], i, iId;
            for (i = 0; (iId = identityId[i]); i++) {
                contactIds.push.apply(contactIds, this._byIdty[iId.join("|")]);
            }
    
            this.get(contactIds, callback, errback);
        },
    
        //Adds the given identity to a contact specified by contact
        //by creating a raindrop document that connects the contact to the identity.
        //This method create the contact document if it does not exist, and also create
        //a rd.identity.contacts reo
        //SHOULD NOT be used if identity existed before, and already has an
        //rd.identity.contacts record. Use merge() for that case.
        // contact:
        //        an object that has either a contactId or name property. If no
        //        contactId, then it is assumed a new contact needs to be created
        //        with the given name property.
        addIdentity: function (/*Object*/contact, /*Object*/identity,
                              /*Function*/callback, /*Function?*/errback) {
            if (contact.contactId) {
                this._makeRdIdentityContacts(identity, contact.contactId, callback, errback);
            } else if (contact.name) {
                //Create the contact record first. Dynamically load the uuid stuff we need
                //for the contacts.
                require(["dojox", "dojox/uuid/generateRandomUuid", "dojox/uuid/Uuid"],
                    dojo.hitch(this, function (dojox) {
                    var uuid = dojox.uuid, uid, apiInst, contactSI;
                    uuid.Uuid.setGenerator(uuid.generateRandomUuid);
                    uid = (new uuid.Uuid()).toString();
    
                    contactSI = {
                        rd_key: ["contact", uid],
                        rd_schema_id: "rd.contact",
                        rd_source: [identity._id],
                        items: {
                            name: contact.name
                        }
                    };
    
                    //Insert the document.
                    apiInst = api().createSchemaItem(contactSI)
                    .ok(this, function (contact) {
                        //Update our cache
                        this._store.push(contact);
                        this._store[uid] = contact;
                        //Finish with creating the rd.identity.contacts record.
                        this._makeRdIdentityContacts(identity, uid, callback, errback);
                    });
                    if (errback) {
                        apiInst.error(errback);
                    }
                }));
            } else if (errback) {
                errback("Invalid contact object passed to rd/contact.addIdentity");
            }
        },
    
        //Merges two contacts by figuring out which one has the fewer
        //number of identities, then changing the identity maps for those identities
        //to point to the other contact. Then deletes the old contact record.
        merge: function (/*String*/sourceContactId, /*String*/targetContactId,
                        /*Function*/callback, /*Function?*/errback) {
            var source = this._store[sourceContactId],
                target = this._store[targetContactId],
                temp, dfds = [], i, idty, dfd, idtyMapDocId, dfdList;
    
            if (target.identities.length < source.identities.length) {
                temp = source;
                source = target;
                target = temp;
            }
    
            //For each identity in source, grab the identity/contact map document
            //and update it to point to the new contactId.
            //TODO: this is really unsafe: just doing a bunch of doc updates
            //and then a contact delete. Need to account for errors better and
            //try to do rollbacks?
            for (i = 0; (idty = source.identities[i]); i++) {
                idtyMapDocId = this._idtyMapIds[idty.rd_key[1].join("|")];
                //TODO: is this broken? does the dfd only ever point to the last
                //dfd created?
                dfd = new dojo.Deferred();
                couch.db("raindrop").openDoc(idtyMapDocId, {
                    success: dojo.hitch(this, function (dfd, json) {
                        //Update the contact ID field with the new field.
                        var i, contacts = json.contacts;
                        for (i = 0; i < contacts.length; i++) {
                            if (contacts[i][0] === source.rd_key[1]) {
                                contacts[i][0] = target.rd_key[1];
                                break;
                            }
                        }

                        contact._saveDoc(json, dfd);
                    }, dfd),
                    error: errback
                });
    
                dfds.push(dfd);
            }
    
            //Now wait for all the contacts to be updated via a DeferredList.
            dfdList = new DeferredList(dfds);
            dfdList.addCallback(dojo.hitch(this, function (response) {
                //The data we have is not incomplete.    Force a refresh of the
                //data. TODO: make this smarter? Just update in memory cache?
                this._reset();
                return response;
            }));
            dfdList.addCallbacks(callback, errback);
    
            //TODO: delete the old contact?
        },

        //Broken out as a separate function to avoid loop issues with closures.
        _saveDoc: function (json, dfd) {
            couch.db("raindrop").saveDoc(json, {
                success: function (json) {
                    dfd.callback(json);
                    return json;
                },
                error: function (response) {
                    dfd.errback(response);
                    return response;
                }
            });
        },

        //Private method that figures out what contacts are already
        //loaded with identities and which ones are missing identities.
        //contactId can be one ID or an array of IDs.
        _get: function (/*String|Array*/contactId) {
            var missing = [], found = [], unknown = [], temp, i, id;
            if (typeof contactId === "string") {
                contactId = [contactId];
            }
            
            for (i = 0; (id = contactId[i]); i++) {
                temp = this._store[contactId];
                (temp && temp.identities ? found.push(temp) : missing.push(id));
            }
    
            return {
                found: found.length ? found : null,
                missing: missing.length ? missing: null,
                unknown: unknown.length ? unknown: null
            };
        },
    
        //Clears the in memory cache and sets up a trigger
        //of data on next public operation.
        _reset: function () {
            this._store = [];
            this._byContact = {};
            this._byIdty = {};
            this._idtyMapIds = {};
            this._listStatus = "unfetched";
    
            this._loaded = false;        
        },
    
        //rd/_api trigger for loading api data.
        _load: function () {
            if (this._store.length) {
                //Already retrieved the contacts and identity/contact mappings.
                //Just need to make sure we do not need to load all identities.
                if (this._listStatus === "needFetch") {
                    this._fetchAllIdentities();
                } else {
                    this._onload();
                }
            } else {
                // open all contacts - note this also includes contacts without
                // associated identities...
                api().megaview({
                    key: ["schema_id", "rd.contact"],
                    include_docs: true,
                    reduce: false
                })
                .ok(this, function (json) {
                    //Error out if no rows return.
                    if (!json.rows.length) {
                        this._error = new Error("no contacts");
                        this._onload();
                    } else {
                        for (var i = 0, row, doc; (row = json.rows[i]) && (doc = row.doc); i++) {
                            this._store.push(doc);
                            // for contacts, rd_key is a tuple of ['contact', contact_id]
                            this._store[doc.rd_key[1]] = doc;
                        }
    
                        this._loadIdtyMapping();
                    }
                })
                .error(this, function (err) {
                    this._error = err;
                    this._onload();
                });
            }
        },
    
        //Loads the contact-identity mapping.
        _loadIdtyMapping: function () {
            api().view('identities_by_contact', {
            })
            .ok(this, function (json) {
                //Error out if no rows return.
                if (!json.rows.length) {
                    this._error = new Error("no contacts");
                    this._onload();
                } else {
                    for (var i = 0, row; (row = json.rows[i]); i++) {
                        // check we really have an identity record...
                        this._mapIdtyToContact(row.value.rd_key, row.id, row.key[0]);
                    }
    
                    if (this._listStatus === "needFetch") {
                        this._fetchAllIdentities();
                    } else {
                        this._onload();
                    }
                }
            })
            .error(this, function (err) {
                this._error = err;
                this._onload();
            });
        },
    
        //Given a new identity, map it correctly to the contact in internal storage
        _mapIdtyToContact: function (/*Object*/idty_rdkey, idty_docid, /*String*/contactId) {
    
            var rdkey = idty_rdkey,
                idid = rdkey[1],
                idtyStringKey = idid.join("|"),
                byContact, byIdty;
    
            //Hold onto the document ID for this identity map,
            //for use in updating/merging identity/contact relationships
            this._idtyMapIds[idtyStringKey] = idty_docid;
        
            //Store identities by contactId
            byContact = this._byContact[contactId];
            if (!byContact) {
                byContact = this._byContact[contactId] = [];
            }
            byContact.push(idid);
    
            //Then store the contact by identity.
            byIdty = this._byIdty[idtyStringKey];
            if (!byIdty) {
                byIdty = this._byIdty[idtyStringKey] = [];
            }
            byIdty.push(contactId);
        },
    
        _loadIdtys: function (/*String|Array*/contactId, /*Function*/callback, /*Function?*/errback) {
            //Loads all the identities for a set of contacts.
    
            //Normalize input to an array.
            if (typeof contactId === "string") {
                contactId = [contactId];
            }
    
            //Gather the identities we need to fetch, based
            //on the contact-to-identity mapping.
            var identityIds = [], i, id, idtyIds;
            for (i = 0; (id = contactId[i]); i++) {
                idtyIds = this._byContact[id];
                if (idtyIds && idtyIds.length) {
                    identityIds.push.apply(identityIds, idtyIds);
                }
            }
    
            //Get identities.
            api().identity({
                ids: identityIds
            })
            .ok(this, function (foundIdtys) {
                var i, idty, ret = [], cId;
                //Normalize input.
                if (typeof foundIdtys === "string") {
                    foundIdtys = [foundIdtys];
                }
    
                for (i = 0; (idty = foundIdtys[i]); i++) {
                    this._attachIdentity(idty);
                }
    
                //Now collect the contacts originally requested and do the callback.
                for (i = 0; (cId = contactId[i]); i++) {
                    ret.push(this._store[cId]);
                }
                callback(ret);
            })
            .error(errback);
        },
        
        //Given an identity, attach it to the cache of the contact in the data store.
        //Use the first part of the identity as a property on the contact. This means for
        //instance, only one twitter account will be on the contact at
        //contact.twitter, but all identities are listed in contact.identities.
        _attachIdentity: function (/*Object*/idty) {
            console.assert(idty.rd_key[0] === 'identity', idty); // not an identity?
            var idid = idty.rd_key[1],
                cIds = this._byIdty[idid.join("|")],
                j, cId, contact, idType;
            if (cIds && cIds.length) {
                for (j = 0; (cId = cIds[j]); j++) {
                    contact = this._store[cId];
                    idType = idid[0];
    
                    //Only keep one property on the object
                    //with the idType, so that means first one
                    //in the list wins for that type of identity.
                    if (!contact[idType]) {
                        contact[idType] = idty;
                    }
    
                    if (!contact.identities) {
                        contact.identities = [];
                    }
    
                    //Make sure we do not add the same contact more than once.
                    if (dojo.indexOf(contact.identities, idty) === -1) {
                        contact.identities.push(idty);
                        
                        //If the contact does not have an image, use one
                        //on the identity if possible. First one with a picture wins.
                        if (idty.image && !contact.image) {
                            contact.image = idty.image;
                        }
                    }
                }
            }
        },
    
        //Fetches all the identities for all the contacts. Normally
        //a response to a rd/contact.list() call.  
        _fetchAllIdentities: function () {
            //Get the list of contactIds that still need to be fetched.
            var contactIds = [], i, contact;
            for (i = 0; (contact = this._store[i]); i++) {
                if (!contact.identities) {
                    contactIds.push(contact.rd_key[1]);
                }
            }
            if (!contactIds.length) {
                this._listStatus = "done";
                this._onload();
            } else {
                //Load the identities.
                this._loadIdtys(
                    contactIds,
                    dojo.hitch(this, function (contacts) {
                        this._listStatus = "done";
                        this._onload();
                    }),
                    dojo.hitch(this, function (err) {
                        this._error = err;
                        this._onload();
                    })
                );
            }
        },
    
        //Just makes an rd.identity.contacts record.
        _makeRdIdentityContacts: function (/*Object*/identity, /*String*/contactId,
                                          /*Function*/callback, /*Function?*/errback) {
            //Generate the new rd.identity.contacts document.
            var idtyMap = {
                rd_key: identity.rd_key,
                rd_schema_id: "rd.identity.contacts",
                rd_source: [identity._id, identity._rev],
                items: {
                    contacts: [
                        [contactId, null]
                    ]
                }
            },
    
            //Insert the document.
            apiInst = api().createSchemaItem(idtyMap)
            .ok(this, function (doc) {
    
                //Update the data store.
                this._mapIdtyToContact(doc.rd_key, doc._id, contactId);
                this._attachIdentity(identity);
    
                callback();
                rd.pub("rd-contact-updated", this._store[contactId]);
            });
    
            if (errback) {
                apiInst.error(errback);
            }
        },
    
        //Sees if there is a match in names with from, and vice versa. Basically,
        //if any name matches with part of from or from matches with a part of a name.
        _hasNameMatch: function (/*String*/from, /*Array*/names) {
            var ret = false, name, i;
            //Do not bother with short one letter names.
            if (from.length > 1) {
                for (i = 0; (name = names[i]); i++) {
                    name = name.toLowerCase();
                    if (name.length > 1 && (from.indexOf(name) !== -1 || name.indexOf(from) !== -1)) {
                        return true;
                    }
                }
            }
            return ret;
        }
    });
    
    contact._protectPublic();
    
    return contact;
});