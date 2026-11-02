# Privacy and local storage — draft

Operator: ayaan. Distribution: worldwide. Effective date: the first public release date.

## Operator access

The app does not transmit documents, questions, answers, API keys or usage records to operator ayaan, and API requests do not pass through an operator server. App-managed storage is local to your device. This does not include content you choose to post publicly in GitHub Issues, or processing by OpenAI described below.

## What stays on your device

The application stores registered folder paths, document indexes, extracted text, embeddings, conversations, saved citations, execution events and request/response snapshots, cost records and preferences in its app storage. Snapshots can contain document content and paths; they are not anonymous diagnostics. There is no automatic retention expiry for these conversation records. They remain until the applicable cleanup action is used. Uninstalling the app does not remove this storage.

Original documents remain in your selected folders. App-data cleanup does not delete those originals. Backups may contain extracted text, vectors and conversations, are not encrypted by the app and remain separate from cleanup. Protect backup files as carefully as your documents.

On Windows, API keys are stored in the OS credential manager and used in process memory for API authentication. The web interface receives registration status, not the saved key. The development web uses a server-configured key; it does not provide key registration. No app account is required; external API access requires your own provider credentials.

## What is sent to OpenAI

Asking a question may send your question, relevant conversation history, document excerpts, tool results and file paths. Remote input-token counting also sends model input before answer generation; it is not a local-only operation. Semantic search sends query text and enabling semantic search sends document text for embeddings. Approved PDF/image preparation sends the selected page/image content for processing. Registering or browsing a folder alone is not permission to upload all its files.

API charges are billed under your provider account, separately from the app. Preparation estimates are estimates, not guaranteed limits. Stopping a request does not recall data already sent or cancel charges already incurred.

OpenAI states that API data is not used for model training unless you opt in. Its default abuse-monitoring retention may be up to 30 days, with exceptions. Setting store=false is not a guarantee of zero retention; endpoint, account and feature rules apply. The published endpoint retention table does not separately list input-token counting, so we do not promise a special retention period for it.

Requests go directly to OpenAI using your API key. Processing follows the OpenAI terms, agreements and data settings applicable to that key’s account, organization and project. The app does not change those settings. API data-sharing controls are separate from ChatGPT training settings.

Provider details: https://developers.openai.com/api/docs/guides/your-data

## Website, cookies and browser storage

The current promotional site source contains no analytics or advertising SDK, contact form, tracking cookie or remotely loaded webfont. No consent banner is added for technologies the site does not use.

GitHub Pages records visitor IP addresses for security. This is hosting-provider processing, not an app tracking cookie. Details: https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages and https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement

The app webview/development web uses localStorage for theme; the development web also stores the agent name there. The development web can keep a local API authentication token in sessionStorage. That token is not the OpenAI API key. LocalStorage persists until cleared; sessionStorage normally lasts for the tab/session, subject to browser restoration. Desktop preferences and credentials are separate from website storage.

## Deletion and requests

Settings → App data offers supported cleanup actions. Conversation deletion removes stored turns, citations and execution records for those conversations. Document-result cleanup does not remove old conversation citations. Disconnect-only preserves app data. Folder-data cleanup removes its connection and owned app data; shared data can remain where other references exist. Separate backups and originals are preserved.

Deleting an API key here removes the locally saved credential; revoke it through your provider account if necessary. Logical database deletion is not guaranteed forensic erasure of free pages, WAL files, old vector versions, filesystem snapshots or backups.

Local buttons do not delete records retained by OpenAI, GitHub or a support recipient. Use the provider's privacy/account procedures for those records. Support is provided only through public GitHub Issues at https://github.com/pleasereadthisforme/pleasereadthisforme/issues . There is no private support channel. Issue posts and attachments are public. Do not post personal data, secrets or document contents. For removal of a project issue, identify the issue URL without repeating sensitive content; edit/delete your own content where available and request maintainer or GitHub assistance for what you cannot remove. Closing an issue does not delete it. Public content may remain until removed; notifications, copies and third-party archives cannot be recalled by the project.

## Policy changes

Material changes to collection or transmission will be described in updated notices before the changed behavior is enabled.
