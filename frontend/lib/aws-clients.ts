import { BedrockRuntimeClient } from "@aws-sdk/client-bedrock-runtime";
import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient } from "@aws-sdk/lib-dynamodb";
import { S3Client } from "@aws-sdk/client-s3";
import { SQSClient } from "@aws-sdk/client-sqs";

// Lazy, env-driven client construction -- mirrors the pattern already used on
// the Python side (common/db.py, common/bedrock.py): endpoint overrides are
// unset in a real deployment (SDK falls back to real AWS) and set for local
// docker-compose.

function region(): string {
  return process.env.AWS_REGION ?? process.env.AWS_DEFAULT_REGION ?? "us-east-1";
}

/**
 * The only S3 usage in this app is minting presigned upload URLs, which the
 * *browser* then PUTs against directly -- so this must be configured with a
 * browser-reachable endpoint (AWS_ENDPOINT_URL_S3_PUBLIC, e.g. localhost),
 * never the docker-network hostname used by server-side-only clients below.
 */
export function getS3Client(): S3Client {
  const endpoint = process.env.AWS_ENDPOINT_URL_S3_PUBLIC;
  return new S3Client({
    region: region(),
    ...(endpoint ? { endpoint, forcePathStyle: true } : {}),
  });
}

/**
 * Server-side S3 calls (e.g. deleting an object) run inside the Next.js
 * container itself, unlike the presigning client above -- so this must use
 * the docker-network-internal endpoint, matching the Python side's
 * `AWS_ENDPOINT_URL_S3` (`http://minio:9000`), never the browser-reachable
 * `_PUBLIC` variant.
 */
export function getS3ServerClient(): S3Client {
  const endpoint = process.env.AWS_ENDPOINT_URL_S3;
  return new S3Client({
    region: region(),
    ...(endpoint ? { endpoint, forcePathStyle: true } : {}),
  });
}

export function getDynamoClient(): DynamoDBDocumentClient {
  const endpoint = process.env.AWS_ENDPOINT_URL_DYNAMODB;
  const client = new DynamoDBClient({
    region: region(),
    ...(endpoint ? { endpoint } : {}),
  });
  return DynamoDBDocumentClient.from(client);
}

export function getSqsClient(): SQSClient {
  const endpoint = process.env.AWS_ENDPOINT_URL_SQS;
  return new SQSClient({
    region: region(),
    ...(endpoint ? { endpoint } : {}),
  });
}

/**
 * Bedrock has no local emulator, so unlike the clients above this can't just
 * flip an endpoint override -- it always talks to real AWS. It also can't
 * share the default credential chain the other clients rely on: local
 * docker-compose sets AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY to dummy values
 * MinIO/DynamoDB-local require (see docker-compose.yml's
 * aws-local-credentials anchor), which would fail against real AWS. Mirrors
 * common/bedrock.py's AWS_BEDROCK_* handling: use those explicitly when set
 * (local dev), otherwise fall through to the default chain (production
 * Lambda/ECS role, where there's no dummy-credential collision).
 */
export function getBedrockClient(): BedrockRuntimeClient {
  const accessKeyId = process.env.AWS_BEDROCK_ACCESS_KEY_ID;
  const secretAccessKey = process.env.AWS_BEDROCK_SECRET_ACCESS_KEY;
  return new BedrockRuntimeClient({
    region: process.env.AWS_BEDROCK_REGION ?? region(),
    ...(accessKeyId && secretAccessKey
      ? {
          credentials: {
            accessKeyId,
            secretAccessKey,
            ...(process.env.AWS_BEDROCK_SESSION_TOKEN
              ? { sessionToken: process.env.AWS_BEDROCK_SESSION_TOKEN }
              : {}),
          },
        }
      : {}),
  });
}
