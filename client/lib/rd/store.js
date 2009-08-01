dojo.provide("rd.store");

dojo.require("couch");

rd.store = {
  megaview: function(/*Object*/args) {
    //summary: thin wrapper around couch megaview call, just to avoid having
    //to remember or copy/paste the megaview url.
    return couch.db("raindrop").view("raindrop!content!all/_view/megaview", args);
  },

  megaviewList: function(/*String*/ listName, /*Object*/args) {
    //summary: thin wrapper around the couch lists for the megaview call.
    return couch.db("raindrop").view("raindrop!content!all/_list/" + listName + "/megaview", args);
  },

  put: function(/*Object*/doc, /*Function*/callback, /*Function?*/errback) {
    //summary: puts a document in the raindrop data store.
    //If successful, callback is called with the doc as the only argument.
    //It will generate the _id and rd_ext_id on the document if it does
    //not exist. Warning: it modifies the doc object.

    //Add generic UI extension ID if needed.
    if (!doc.rd_ext_id) {
      doc.rd_ext_id = rd.uiExtId;
    }

    //Generate the ID for the document, if needed.
    if (!doc._id) {
      doc._id = "rc!"
              + doc.rd_key[0]
              + "."
              + rd.toBase64(doc.rd_key[1])
              + "!"
              + doc.rd_ext_id
              + "!"
              + doc.rd_schema_id;
    }

    var docUrl = rd.dbPath + doc._id;
    if (doc._rev) {
      docUrl += "?rev=" + doc._rev;
    }

    dojo.xhrPut({
      url: docUrl,
      putData: dojo.toJson(doc),
      load: function(response, ioArgs) {
        if (response.rev) {
          doc._rev = response.rev;
        }
        callback(doc);
      },
      error: errback
    });
  }
}
