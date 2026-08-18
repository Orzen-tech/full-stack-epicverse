# EpicVerse Security Policy & Firebase Hardening Guidelines

This document outlines security policies, API key restriction procedures, and backend security rules for the **EpicVerse** platform.

---

## 1. Firebase API Key & Client Configuration (`GoogleService-Info.plist`)

### Overview
In Firebase mobile architecture, configuration files such as `GoogleService-Info.plist` (iOS) and `google-services.json` (Android) are embedded in the application binary to route client requests to the designated Firebase project.

The Firebase API Key embedded in these files functions as a **public client identifier**, not a administrative secret. To prevent unauthorized use of this API Key outside the official mobile application, the following Google Cloud restrictions and Firebase security controls must be configured in the Firebase / GCP Console:

---

## 2. Recommended API Key Restrictions (Google Cloud Console)

1. **Application Restrictions (iOS Bundle ID):**
   - Navigate to **Google Cloud Console** -> **APIs & Services** -> **Credentials**.
   - Select the API Key associated with the iOS Firebase app.
   - Under **Application restrictions**, select **iOS apps**.
   - Add your official bundle identifier: `com.kriyora.epicverse` (or production bundle ID).
   - Save changes. This ensures requests originating from unauthorized bundle IDs or web apps are rejected.

2. **API Restrictions:**
   - Restrict the key to only the specific Google APIs required by EpicVerse (e.g., Firebase Authentication, Cloud Firestore, Cloud Storage, Firebase Cloud Messaging).

---

## 3. Firebase Backend Security Rules

Since client-side keys are public, access control is governed entirely by server-side security rules:

1. **Firestore & Cloud Storage Security Rules:**
   - Ensure all database and storage rules require authenticated requests:
     ```javascript
     rules_version = '2';
     service cloud.firestore {
       match /databases/{database}/documents {
         match /{document=**} {
           allow read, write: if request.auth != null;
         }
       }
     }
     ```

2. **Firebase App Check (Recommended for Production):**
   - Enable **Firebase App Check** with **Apple DeviceCheck** / **App Attest** for iOS.
   - App Check attests that requests entering Firebase services originate exclusively from untampered, legitimately signed EpicVerse app binaries.

---

## 4. OWASP MASVS Security Compliance Summary

EpicVerse implements defense-in-depth across:
- **MASVS-CODE-4:** Sanitized log outputs (no PII, OTPs, or tokens in logs).
- **MASVS-AUTH:** Strict password policy and multi-factor authentication (2FA).
- **MASVS-RESILIENCE:** Dart binary symbol obfuscation (`--obfuscate`) and debug symbol stripping (`COPY_PHASE_STRIP`).
- **MASTG-NETWORK-1:** Server-side token validation and rate-limiting (`429 Too Many Requests`).
