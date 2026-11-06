# Cloud Sync: the Google client

Cloud Sync ([spec 14](../specification/14-cloud-sync.md), [ADR-0003](../adr/0003-cloud-sync.md)) signs in to
Google Drive with an OAuth client of type *TVs and Limited Input devices*. Until that client exists, the
application has no client id and Cloud Sync is unavailable: the UI shows no Cloud Sync panel.

The client must be in the **same Google Cloud project as the recipe editor** (its `docs/google-setup.md`): with
the scope `drive.file`, Google shows an application only the files of its own project, and the recipe editor
creates the recipe files.

## Create the client

In the editor's project at [console.cloud.google.com](https://console.cloud.google.com):

1. **Google Auth Platform → Branding**: set the app name to `BrewberryPi`. Google shows it on the consent
   screen of both the editor and the device.
2. **Google Auth Platform → Data access**: the scopes are `openid`, `email` and
   `https://www.googleapis.com/auth/drive.file` (the editor already has `drive.file`). All are non-sensitive.
3. **Google Auth Platform → Clients → Create client**: type **TVs and Limited Input devices**, name
   `BrewberryPi device`. Note the **client ID** and the **client secret**.

Google does not treat the secret of this client type as confidential: it is shipped with every device, so both
values are public.

## Put it into the application

Either build it in: set `BUILT_IN_CLIENT_ID` and `BUILT_IN_CLIENT_SECRET` in
`src/brewberrypi/cloud_sync/google_drive.py` and release the app; or, for one device, set it in its
`config.json`:

```json
{
  "cloudSync": {
    "googleDrive": {
      "clientId": "123-abc.apps.googleusercontent.com",
      "clientSecret": "GOCSPX-…"
    }
  }
}
```

and restart the application.

## Check it once

ADR-0003 relies on two facts that could not be checked when it was written. Check them with the new client:

1. **Connect** works: the Cloud Sync panel shows a code, and after entering it at google.com/device the panel
   says `Connected to Google Drive (<account>)`. If Google refuses the scope `drive.file` for this client type,
   the sign-in fails at once with an error from Google.
2. **The device sees the editor's recipes**: save a recipe in the editor with the same Google account; after
   *Sync now* it is in the recipe list. If the panel says `The folder BrewberryPi/recipes was not found in
   Google Drive` although the editor shows recipes, Google does not share `drive.file` grants between the clients
   of one project, and ADR-0003 has to be revisited.
