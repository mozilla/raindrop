dojo.provide("rdw.Message");

dojo.require("rdw._Base");
dojo.require("rdw.gravatar");

dojo.declare("rdw.Message", [rdw._Base], {
  //Suggested values for type are "topic" and "reply"
  type: "topic",

  //Holds the couch document for this story.
  //Warning: this is a prototype property: be sure to
  //set it per instance.
  doc: {},

  templatePath: dojo.moduleUrl("rdw.templates", "Message.html"),

  blankImgUrl: dojo.moduleUrl("rdw.resources", "blank.png"),
  
  postMixInProperties: function() {
    //summary: dijit lifecycle method
    this.inherited("postMixInProperties", arguments);
    
    //Set the properties for this widget based on doc
    //properties.
    //TODO: some of these need more info from backend.    
    // XXX: these are a couple hacks to get the UI looking more like we want
    this.fromName = this.doc.from[1];
    try {
      var pieces = this.doc.from[1].split("<");
      if(pieces && pieces[0]) {
        this.fromName = pieces[0];
      }
    } catch(ignore) { }
    
    this.fromId = this.doc.from[1];
    try {
      var matches = this.doc.from[1].match(/<(.+)>/);
      if(matches && matches[1]) {
        this.fromId = matches[1].toLowerCase();
      }
    } catch(ignore) { }

    this.subject = null;
    try {
      this.subject = rd.escapeHtml(this.doc.subject.replace(/^Re:/,''));
    } catch(ignore_empty_subjects) { }

    this.message = rd.escapeHtml(this.doc.body_preview);
    this.time = 0;
    this.timeDisplay = rd.escapeHtml("some time ago");
    
    this.userPicUrl = this.blankImgUrl;
    //If the fromId has an @ in it, try to use a gravatar for it.
    if (this.fromId && this.fromId.indexOf("@") != -1) {
      this.userPicUrl = rdw.gravatar.get(this.fromId);
    }
  },

  postCreate: function() {
    //summary: dijit lifecycle method
    this.inherited("postCreate", arguments);
    
    
  },

  onToolClick: function(evt) {
    //summary: handles clicks for tool actions. Uses event
    //delegation to publish the right action.
    var href = evt.target.href;
    if (href && (href = href.split("#")[1])) {
      rd.pub("rdw.Message-" + href, {
        node: this.toolDisplay,
        doc: this.doc
      });
      evt.preventDefault();
    }
  }
});