"""Editing files of files tables in an ONLYOFFICE Document Server (Docs), inside Probyte's file viewer.

How it fits together (ONLYOFFICE's Docs API, with JWT signing):
1. The viewer asks for an editor (POST /api/office/sessions): Probyte makes a session with a secret token and
   returns the editor's settings, signed with the Document Server's secret (JWT).
2. The browser loads the Document Server's editor (api.js) with them. The Document Server downloads the file from
   Probyte (GET /api/office/file/<token>) and calls back (POST /api/office/callback/<token>) as people edit.
3. A save (the Save button: a "forcesave" command; or everyone closing the editor) makes the Document Server call
   back with the new version's address: Probyte downloads it and writes it to the S3 / WebDAV storage, as a new
   version of the same record.
4. Closing with "Discard": the session is marked, and the Document Server's final save is ignored when everyone
   who changed the document discarded (someone editing along who didn't keeps their changes). The next editor gets
   a new key, so it starts again from the file in the storage.

Files of linked tables are edited in this server's Document Server too: Probyte hands it the file it gets through
the link, and saves go back through the link (when its owner lets it change files: "Edit files").

Old formats (.doc, .xls, .ppt…) can't be saved as they are: the Document Server first converts them (its conversion
service) and the editor works on the .docx / .xlsx / .pptx. Saving makes that the file's new version (report.doc
becomes report.docx, same record); discarding leaves the old file as it was.

Browsers can reach the Document Server through Probyte ("through Probyte", the default for new setups): Probyte
relays it at /office-ds/ (pages, files and the editor's WebSocket), so it works wherever Probyte does, over HTTPS,
with the Document Server only on the local network or the same Docker network. Otherwise browsers load it from its
own address, which they must be able to reach.

The Document Server must reach Probyte (its file and callback addresses), and Probyte must reach the Document
Server (commands, and the saved files): settings "Probyte's address for the Document Server" and "the Document
Server's address for Probyte" cover setups where these differ from the public addresses.

    config.py     the settings, and which files open how
    jwt.py        signing and checking (HS256), as the Document Server does
    docserver.py  Probyte as the Document Server's client: commands, conversions, saved versions
    sessions.py   editors, document keys, and what happens to saved versions
    relay.py      the Document Server through Probyte for browsers (/office-ds/)
    api.py        the HTTP routes of all that
"""
from fastapi import APIRouter

from app.office import api, relay

router = APIRouter()
router.include_router(api.router)
router.include_router(relay.router)
