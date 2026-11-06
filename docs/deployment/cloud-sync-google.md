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

## Put it into every release

The values are not in git: the repository is public, and GitHub's secret scanning flags Google client secrets.
Set them once in the app repository, **Settings → Secrets and variables → Actions**:

| Name | Kind | Value |
|---|---|---|
| `GOOGLE_DRIVE_CLIENT_ID` | Variable | the client ID (`….apps.googleusercontent.com`) |
| `GOOGLE_DRIVE_CLIENT_SECRET` | Secret | the client secret (`GOCSPX-…`) |

CI hands both to the image build as build secrets (`.github/workflows/ci.yml`, job *Container image*). The
Containerfile writes them with `scripts/google-drive-client.py` into the package file
`brewberrypi/data/google-drive-client.json` (git-ignored), which the application reads as its built-in client.
A release build stops if either value is missing. Builds without them, such as `make image` or CI for a fork,
have no built-in client, so Cloud Sync is unavailable there.

The values end up readable in the published image. That is expected for this client type.

To try a client on one device without a release, or to use your own client, set it in that device's
`config.json`; it replaces the built-in client:

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

For local development, the same JSON as the package file (`{"clientId": "…", "clientSecret": "…"}`) in
`src/brewberrypi/data/google-drive-client.json` also works.

To replace the secret, change it in the Actions settings and release a new version.

## Check it once

ADR-0003 relies on two facts that could not be checked when it was written. Check them with the new client:

1. **Connect** works: the Cloud Sync panel shows a code, and after entering it at google.com/device the panel
   says `Connected to Google Drive (<account>)`. If Google refuses the scope `drive.file` for this client type,
   the sign-in fails at once with an error from Google.
2. **The device sees the editor's recipes**: save a recipe in the editor with the same Google account; after
   *Sync now* it is in the recipe list. If the panel says `The folder BrewberryPi/recipes was not found in
   Google Drive` although the editor shows recipes, Google does not share `drive.file` grants between the clients
   of one project, and ADR-0003 has to be revisited.
