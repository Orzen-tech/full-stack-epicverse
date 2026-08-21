# Security Policy — EpicVerse

## Firebase API Key Restrictions (Verified ✅ — 21 Aug 2026)

| API Key | Application Restriction | API Scope Restriction | Status |
| :--- | :--- | :--- | :--- |
| iOS key (auto created by Firebase) | ✅ iOS apps — `com.kriyora.epicverse` | ✅ 25 Firebase APIs | **Fully Restricted** |
| Android key (auto created by Firebase) | ⚠️ None set | ✅ 25 Firebase APIs | Recommend adding Android bundle restriction |
| Browser key (auto created by Firebase) | ⚠️ None set | ✅ 25 Firebase APIs | For browser/web use only |

---

## Firebase Configuration File Exposure (`GoogleService-Info.plist`)

### Observation
During static analysis of the iOS IPA, the `GoogleService-Info.plist` file bundled within the application was found to contain Firebase project configuration values:

| Key | Value Type | Notes |
| :--- | :--- | :--- |
| `API_KEY` | Firebase Web API Key | Client-side identifier, not a backend secret |
| `PROJECT_ID` | Firebase Project ID | Public identifier |
| `GOOGLE_APP_ID` | Google App ID | Public iOS app identifier |
| `CLIENT_ID` | OAuth 2.0 Client ID | Used for Google Sign-In flows |
| `STORAGE_BUCKET` | Firebase Storage bucket | Public identifier |

### Risk Assessment
This is a **known and expected characteristic** of all Firebase iOS applications. Google explicitly states that the Firebase API key is **not a secret** — it is a client-side identifier designed to be bundled in mobile apps to identify the Firebase project. It cannot be used server-side to authenticate as a user or access protected resources.

**However, the security of the configuration depends entirely on server-side rules being correctly configured.**

### Current Server-Side Protections (Verified ✅)

| Protection Layer | Status | Details |
| :--- | :--- | :--- |
| Firestore Security Rules | ✅ **Enforced** | `allow read, write: if request.auth != null && request.auth.uid == userId` — only authenticated users can access their own data |
| Default Deny Rule | ✅ **Enforced** | `allow read, write: if false` — all unmatched paths are denied by default |
| Backend API Auth | ✅ **Enforced** | All sensitive backend routes require `Authorization: Bearer <Firebase ID Token>` |
| Invite Code Enforcement | ✅ **Enforced** | New user registration requires a valid invite code |

### Remediation Actions Taken

1. **`GoogleService-Info.plist` removed from Git tracking** (`git rm --cached`) — the file will no longer be committed to the repository going forward.
2. **Root `.gitignore` updated** — `**/GoogleService-Info.plist` and `**/google-services.json` added to prevent future accidental commits.
3. **Note:** The file exists in prior Git commit history. If a full history purge is needed, a `git filter-branch` or BFG Repo Cleaner rewrite should be performed.

### Recommended Manual Action (Firebase Console)

To add an additional layer of protection, please verify or apply API key restrictions in the **Google Cloud Console**:

1. Go to **Google Cloud Console** → **APIs & Services** → **Credentials**
2. Select the API key used by EpicVerse (`AIzaSy...`)
3. Under **Application restrictions**, set:
   - `iOS apps` → add bundle ID `com.kriyora.epicverse`
4. Under **API restrictions**, restrict to only the Firebase APIs used:
   - Firebase Installations API
   - Cloud Firestore API
   - Firebase Authentication API
   - Firebase Cloud Messaging API
5. Click **Save**

This ensures the API key can only be used from the EpicVerse iOS bundle ID and only for the permitted Firebase APIs.

---

## Reporting Security Vulnerabilities

If you discover a security vulnerability in EpicVerse, please report it responsibly by emailing the security team. Do not open public GitHub issues for security findings.
