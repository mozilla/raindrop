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

/*global require: false */
"use strict";

require.def("rdw/conversation/FullMessage",
["dojo", "rdw/Message", "text!rdw/conversation/templates/FullMessage!html"],
function (dojo, Message, template) {
    return dojo.declare("rdw.conversation.FullMessage", [Message], {
        templateString: template,

        postMixInProperties: function () {
            //summary: dijit lifecycle method
            this.inherited("postMixInProperties", arguments);
  
            //Collapse quote regions in the text and hyperlink things.
            //TODO: make message transforms extensionized.
            this.message = this.formatQuotedBody();
        }
    });
});
