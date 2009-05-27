dojo.provide("rd.message");

dojo.require("couch");

rd.message = function(/*String|Array*/ids, /*Function*/callback, /*Function*/errback) {
  //summary: gets a message given an ID. Loads multiple couch documents to create
  //a unified message object for the UI. The method accepts either one string message
  //ID or an array of string message IDs. Normally, you will want to call rd.conversation
  //to get a message combined with all other messages in a certain conversation.

  //Find out if this is just one ID.
  var isOne = typeof ids == "string";
  if (isOne) {
    ids = [ids];
  }

  //Generate proper key for megaview lookup.
  var keys = [];
  for (var i = 0, id; id = ids[i]; i++) {
    keys.push(['rd/core/content', 'key', id]);
  }

  couch.db("raindrop").view("raindrop!content!all/_view/megaview", {
    keys: keys,
    include_docs: true,
    reduce: false,
    success: dojo.hitch(this, function(json, ioArgs) {
      if(!json.rows.length) {
        errback && errback(new Error("no message with ID: " + id));
      } else {
        var messageResults = [];
        var bag = {};

        for (var i = 0, row, doc; ((row = json.rows[i]) && (doc = row.doc)); i++) {
          //Make sure we have the right aggregate to use for this row.
          var rdKey = doc.rd_key;
          var schemaId = doc.rd_schema_id;

          //Skip some schemas since it is extra stuff we do not need.
          //Prefer a blacklist vs. a whitelist, since user extensions may add
          //other things, and do not want to have extensions register extra stuff? TODO.
          if (schemaId.indexOf("/rfc822") == -1 && schemaId.indexOf("/raw") == -1) {
            // TODO: note that we may get many of the same schema, which implies
            // we need to aggregate them - tags is a good example.  For
            // now just make noise...
            if (bag[schemaId]) {
              console.warn("message", doc.rd_key, "has multiple", schemaId, "schemas");
            }
            // for now it gets clobbered if it exists...
            bag[schemaId] = row.doc;
          }

          //See if all schemas are loaded for a message bag by checking the next
          //document in the json results to see if it has a differen rd_key.
          //If so, then tell any extensions about new message load.
          var nextDoc = json.rows[i + 1] && json.rows[i + 1].doc;
          if (!nextDoc || rdKey[1] != nextDoc.rd_key[1]) {
            //Have a final bag. Make sure it is not a ghost
            if (bag["rd/msg/body"]) {
              messageResults.push(bag);
              rd.message.onMessageLoaded(bag);
            }
            //Reset the bag.
            bag = {};
          }
        }

        if (isOne) {
          messageResults = messageResults[0];
        }
        callback(messageResults);
      }
    }),
    error: errback
  });
}

dojo._mixin(rd.message, {
  onMessageLoaded: function(/*Object*/messageBag) {
    //summary: an extension point to allow adding things to the
    //message bag before it is returned to a caller. This is how
    //to add UI data extensions for messages.
  }
});
