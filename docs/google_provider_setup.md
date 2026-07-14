# Unified Google Provider Setup Guide

Welcome to the unified Google Provider for Kazma! This module leverages the modern `google-genai` Python SDK to support both **Google AI Studio** and **Vertex AI** seamlessly.

Follow this guide to configure your preferred Google product.

---

## 🚀 Step 1: Select Your Active Google Product Mode

In your Kazma Settings UI, choose one of the following product modes:

1. **Google AI Studio (Gemini API):**
   * **Best for:** Rapid development, hobbyists, low-latency, and zero-configuration cloud setups.
   * **Authentication:** Authenticates using a developer API key.
2. **Vertex AI (Google Cloud Platform):**
   * **Best for:** Enterprise deployments, compliance-heavy workloads, and existing Google Cloud environments.
   * **Authentication:** Authenticates locally via Application Default Credentials (ADC). Requires a GCP Project ID and region (location).

---

## 🔑 Option A: Setting Up Google AI Studio (Gemini API)

1. **Obtain an API Key:**
   * Navigate to the [Google AI Studio Console](https://aistudio.google.com/).
   * Click **Get API Key** and generate a new key for your project.
2. **Configure in Kazma:**
   * Set **Google Product Mode** to `AI Studio`.
   * Input your newly generated API key in the **API Key** input field.
   * *(Optional)* Alternatively, set the key as an environment variable in your terminal:
     ```bash
     export GEMINI_API_KEY="your-api-key-here"
     ```

---

## ☁️ Option B: Setting Up Vertex AI (Google Cloud Platform)

1. **Prerequisites:**
   * Install the [Google Cloud CLI (gcloud)](https://cloud.google.com/sdk/docs/install).
   * Ensure your GCP project has the **Vertex AI API** (`aiplatform.googleapis.com`) enabled.
2. **Authenticate Locally (Application Default Credentials):**
   * Run the following command in your terminal to authenticate your local developer environment using your personal credentials:
     ```powershell
     gcloud auth application-default login
     ```
   * This command will open a web browser for you to sign in to your Google Account and will securely store your credentials locally where the SDK can find them automatically.
3. **Configure in Kazma:**
   * Set **Google Product Mode** to `Vertex AI`.
   * Input your **GCP Project ID** in the corresponding UI field.
   * Set your preferred deployment region (e.g., `us-central1` or `europe-west4`) in the **Location** field.

---

## 🛠️ Specialized Gemini Code Assist

Our Google Provider includes a specialized **Code Assist wrapper** that replicates the model's native code execution capabilities. When calling `generate_code`:
* It automatically enables the official Google **Code Execution Tool**.
* This enables models like `gemini-2.5-pro` to write, compile, run, and iteratively self-correct Python code internally in a sandbox before producing the final, pristine answer.
