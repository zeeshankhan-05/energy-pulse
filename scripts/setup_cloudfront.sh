#!/usr/bin/env bash
# setup_cloudfront.sh — Create a CloudFront distribution in front of the EC2 origin.
#
# Uses HTTP-only to EC2 (port 80); CloudFront terminates HTTPS for viewers.
# No custom domain required — uses the *.cloudfront.net default domain.
#
# Required env vars (or edit the defaults below):
#   AWS_ACCOUNT_ID  — defaults to 176322301225
#   AWS_REGION      — CloudFront is global but CLI region is used for auth

set -euo pipefail

AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-176322301225}"
AWS_REGION="${AWS_REGION:-us-east-2}"
EC2_IP="18.221.111.39"
# CloudFront does not accept raw IPs as origin DomainName — use the EC2 public DNS.
EC2_HOST="ec2-18-221-111-39.us-east-2.compute.amazonaws.com"
CALLER_REF="energypulse-$(date +%s)"
DIST_CONFIG_FILE="$(mktemp /tmp/cf-dist-XXXXX.json)"
trap 'rm -f "$DIST_CONFIG_FILE"' EXIT

echo "==> [1/3] Building distribution config (CallerRef: ${CALLER_REF})..."

cat > "$DIST_CONFIG_FILE" <<EOF
{
  "CallerReference": "${CALLER_REF}",
  "Comment": "EnergyPulse — EC2 ${EC2_IP}",
  "DefaultRootObject": "index.html",
  "Enabled": true,
  "PriceClass": "PriceClass_100",

  "Origins": {
    "Quantity": 1,
    "Items": [
      {
        "Id": "energypulse-ec2",
        "DomainName": "${EC2_HOST}",
        "CustomOriginConfig": {
          "HTTPPort": 80,
          "HTTPSPort": 443,
          "OriginProtocolPolicy": "http-only",
          "OriginSslProtocols": {
            "Quantity": 1,
            "Items": ["TLSv1.2"]
          },
          "OriginReadTimeout": 30,
          "OriginKeepaliveTimeout": 5
        }
      }
    ]
  },

  "CacheBehaviors": {
    "Quantity": 1,
    "Items": [
      {
        "PathPattern": "/api/*",
        "TargetOriginId": "energypulse-ec2",
        "ViewerProtocolPolicy": "redirect-to-https",
        "AllowedMethods": {
          "Quantity": 7,
          "Items": ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"],
          "CachedMethods": {
            "Quantity": 2,
            "Items": ["GET", "HEAD"]
          }
        },
        "ForwardedValues": {
          "QueryString": true,
          "Cookies": { "Forward": "none" },
          "Headers": {
            "Quantity": 5,
            "Items": ["Authorization", "Content-Type", "Accept", "Origin", "X-Forwarded-Proto"]
          }
        },
        "MinTTL": 0,
        "DefaultTTL": 0,
        "MaxTTL": 0,
        "Compress": false
      }
    ]
  },

  "DefaultCacheBehavior": {
    "TargetOriginId": "energypulse-ec2",
    "ViewerProtocolPolicy": "redirect-to-https",
    "AllowedMethods": {
      "Quantity": 7,
      "Items": ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"],
      "CachedMethods": {
        "Quantity": 2,
        "Items": ["GET", "HEAD"]
      }
    },
    "ForwardedValues": {
      "QueryString": true,
      "Cookies": { "Forward": "none" },
      "Headers": {
        "Quantity": 0,
        "Items": []
      }
    },
    "MinTTL": 0,
    "DefaultTTL": 86400,
    "MaxTTL": 31536000,
    "Compress": true
  },

  "CustomErrorResponses": {
    "Quantity": 2,
    "Items": [
      {
        "ErrorCode": 403,
        "ResponsePagePath": "/index.html",
        "ResponseCode": "200",
        "ErrorCachingMinTTL": 0
      },
      {
        "ErrorCode": 404,
        "ResponsePagePath": "/index.html",
        "ResponseCode": "200",
        "ErrorCachingMinTTL": 0
      }
    ]
  }
}
EOF

echo "    Config written to ${DIST_CONFIG_FILE}"

# ─── Create the distribution ──────────────────────────────────────────────────
echo ""
echo "==> [2/3] Creating CloudFront distribution (this takes ~1 minute for the API call)..."

RESPONSE=$(aws cloudfront create-distribution \
  --distribution-config "file://${DIST_CONFIG_FILE}" \
  --region "${AWS_REGION}" \
  --output json)

DIST_ID=$(echo "$RESPONSE"   | python3 -c "import sys,json; print(json.load(sys.stdin)['Distribution']['Id'])")
CF_DOMAIN=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['Distribution']['DomainName'])")
STATUS=$(echo "$RESPONSE"    | python3 -c "import sys,json; print(json.load(sys.stdin)['Distribution']['Status'])")

# ─── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "==> [3/3] Distribution created."
echo ""
echo "============================================================"
echo " CloudFront distribution ready!"
echo "============================================================"
echo " Distribution ID : ${DIST_ID}"
echo " Status          : ${STATUS}  (→ 'Deployed' in ~10-15 min)"
echo " EC2 origin      : http://${EC2_IP}"
echo ""
echo " URL (open now — HTTP redirects to HTTPS automatically):"
echo ""
echo "   https://${CF_DOMAIN}"
echo ""
echo " To check deployment status:"
echo "   aws cloudfront get-distribution --id ${DIST_ID} \\"
echo "     --query 'Distribution.Status' --output text"
echo "============================================================"
