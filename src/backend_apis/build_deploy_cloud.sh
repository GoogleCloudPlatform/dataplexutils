# Copyright 2024 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

export PROJECT_ID="<TO_DO_DEVELOPER>"
export LOCATION="<TO_DO_DEVELOPER>"
export SERVICE_NAME="metadata-wizard"
# Dedicated least-privilege runtime service account (do NOT use default Compute Engine Editor SA).
export SERVICE_ACCOUNT="metadata-wizard-sa@${PROJECT_ID}.iam.gserviceaccount.com"
export METADATA_WIZARD_ALLOWED_PROJECTS="${METADATA_WIZARD_ALLOWED_PROJECTS:-${PROJECT_ID}}"
export METADATA_WIZARD_ALLOWED_DOC_BUCKETS="${METADATA_WIZARD_ALLOWED_DOC_BUCKETS:-<TO_DO_DEVELOPER_DOC_BUCKET>}"
export METADATA_WIZARD_CORS_ORIGINS="${METADATA_WIZARD_CORS_ORIGINS:-http://localhost:3000}"

# Fail deployment if API token is not set (in production, prefer --set-secrets with Secret Manager).
: "${METADATA_WIZARD_API_TOKEN:?Set METADATA_WIZARD_API_TOKEN (or configure Secret Manager --set-secrets) before deploying}"

gcloud builds submit --tag "gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
gcloud run deploy "${SERVICE_NAME}" \
  --image "gcr.io/${PROJECT_ID}/${SERVICE_NAME}" \
  --platform managed \
  --region "${LOCATION}" \
  --service-account "${SERVICE_ACCOUNT}" \
  --no-allow-unauthenticated \
  --set-env-vars "^;^METADATA_WIZARD_API_TOKEN=${METADATA_WIZARD_API_TOKEN};METADATA_WIZARD_ALLOWED_PROJECTS=${METADATA_WIZARD_ALLOWED_PROJECTS};METADATA_WIZARD_ALLOWED_DOC_BUCKETS=${METADATA_WIZARD_ALLOWED_DOC_BUCKETS};METADATA_WIZARD_CORS_ORIGINS=${METADATA_WIZARD_CORS_ORIGINS};METADATA_WIZARD_ALLOW_DIRECT_BQ_WRITE=false;METADATA_WIZARD_ALLOW_REPLACE=false"
