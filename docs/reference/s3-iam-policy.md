# S3 IAM Policy Template

Use this policy as a starting point for the AWS credentials BaseRender stores in `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`. It grants read and write access to one bucket and explicitly omits delete permissions.

BaseRender uses S3 for:

- listing and reading source media under the configured `media_prefix` in `config/defaults.json`
- presigned GET URLs so FFmpeg can read linked media
- job state JSON at `BASERENDER_JOB_STATE_KEY`
- render artifacts (timeline, LUTs) under `BASERENDER_ARTIFACT_PREFIX`
- finished renders under the resolved output prefix (`BASERENDER_OUTPUT_PREFIX` scoped to the media prefix)

The API never calls `DeleteObject` during normal operation. Cancelling a job overwrites job state in place.

Replace the placeholders before attaching the policy to an IAM user or role:

- `YOUR_BUCKET_NAME`
- `YOUR_ACCOUNT_ID` (only needed if you add bucket-policy examples elsewhere)
- prefix paths if you changed the defaults in `apps/api/.env.example`

## Full-bucket template (read/write, no delete)

Use this when BaseRender is the only consumer of the bucket, or when prefix scoping is handled outside IAM.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListBucket",
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::YOUR_BUCKET_NAME"
    },
    {
      "Sid": "ReadWriteObjectsNoDelete",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject"
      ],
      "Resource": "arn:aws:s3:::YOUR_BUCKET_NAME/*"
    }
  ]
}
```

`s3:GetObject` covers object downloads and `HeadObject` calls (used to read output file size).

## Prefix-scoped template (recommended)

Use this when the bucket holds other data, or when you want least-privilege access aligned with the default BaseRender env vars:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListMediaPrefix",
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::YOUR_BUCKET_NAME",
      "Condition": {
        "StringLike": {
          "s3:prefix": [
            "projects/demo",
            "projects/demo/*"
          ]
        }
      }
    },
    {
      "Sid": "ReadWriteMediaAndOutputs",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject"
      ],
      "Resource": [
        "arn:aws:s3:::YOUR_BUCKET_NAME/projects/demo/*"
      ]
    },
    {
      "Sid": "ReadWriteArtifactsAndJobState",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject"
      ],
      "Resource": [
        "arn:aws:s3:::YOUR_BUCKET_NAME/baserender/*"
      ]
    }
  ]
}
```

Adjust the `projects/demo` paths to match `media_prefix` in `config/defaults.json`. Render outputs are written under that prefix plus `BASERENDER_OUTPUT_PREFIX` (default `outputs`), so `projects/demo/*` covers both source media and `projects/demo/outputs/...` renders. Artifacts and job state live under `BASERENDER_ARTIFACT_PREFIX` (default `baserender`).

## Actions intentionally omitted

Do not add these if you want a no-delete policy:

| Action | Why omit |
| --- | --- |
| `s3:DeleteObject` | Removes objects permanently |
| `s3:DeleteObjectVersion` | Removes versioned objects |
| `s3:PutObjectAcl`, `s3:PutBucketPolicy`, `s3:PutBucketAcl` | Bucket/object ownership changes |
| `s3:*` | Over-broad; includes delete and admin actions |

## Attach the policy

1. Create an IAM user or role for BaseRender (for example `baserender-api`).
2. Create a customer-managed IAM policy from one of the JSON templates above.
3. Attach the policy to that user or role.
4. Create an access key pair and copy it into `apps/api/.env`:

   ```env
   BASERENDER_S3_BUCKET=your-bucket
   AWS_ACCESS_KEY_ID=...
   AWS_SECRET_ACCESS_KEY=...
   AWS_REGION=us-east-1
   ```

   Set `media_prefix` in `config/defaults.json` (for example `"projects/demo/"`) to scope bucket browsing and output paths.

The worker does not talk to S3 directly. It downloads artifacts and uploads renders through presigned URLs issued by the API, so the API credentials need the permissions above.

## MediaConvert and EventBridge (hybrid render path)

When the API submits MediaConvert jobs or publishes orchestration events (Phase 3+), extend the IAM policy with these actions. Replace `YOUR_MEDIACONVERT_ROLE_ARN` with the role MediaConvert assumes (`BASERENDER_MEDIACONVERT_ROLE_ARN`).

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "MediaConvertJobControl",
      "Effect": "Allow",
      "Action": [
        "mediaconvert:CreateJob",
        "mediaconvert:GetJob",
        "mediaconvert:DescribeEndpoints"
      ],
      "Resource": "*"
    },
    {
      "Sid": "PassMediaConvertRole",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "YOUR_MEDIACONVERT_ROLE_ARN",
      "Condition": {
        "StringEquals": {
          "iam:PassedToService": "mediaconvert.amazonaws.com"
        }
      }
    },
    {
      "Sid": "PublishOrchestrationEvents",
      "Effect": "Allow",
      "Action": "events:PutEvents",
      "Resource": "arn:aws:events:YOUR_REGION:YOUR_ACCOUNT_ID:event-bus/*"
    }
  ]
}
```

MediaConvert also needs its own IAM role with S3 read/write on the bucket (source media, artifacts, working directory, and outputs). That role is separate from the API user/role above.

## EventBridge rules (Phase 5 — manual setup)

There is no IaC in this repository. Configure these rules in the AWS console (or your own IaC) after deploying the render and notifier Lambdas:

| Rule | Event pattern | Target |
| --- | --- | --- |
| **MediaConvert completion** | Source `aws.mediaconvert`, detail-type `MediaConvert Job State Change`, status `COMPLETE` / `ERROR` / `CANCELED` | Notifier Lambda (`baserender_lambda.notifier.lambda_handler`) |
| **Lambda shot complete** | Source `baserender` (or `BASERENDER_EVENT_SOURCE`), detail-type `BaseRender Shot Complete` | Notifier Lambda |
| **Lambda shot render** | Source `baserender`, detail-type `BaseRender Lambda Shot` | Render Lambda (`baserender_lambda.handler.lambda_handler`) |

The **notifier Lambda** needs `BASERENDER_API_BASE_URL` and `BASERENDER_WORKER_TOKEN` to POST completion events to `POST /internal/events` on the API.

The **render Lambda** needs `BASERENDER_S3_BUCKET`, `BASERENDER_EVENT_BUS` (to emit shot-complete events), and an FFmpeg Lambda layer.

Additional IAM for rule management (if the deployer creates rules programmatically):

| Action | Purpose |
| --- | --- |
| `events:PutRule`, `events:PutTargets`, `events:DescribeRule` | Create/update EventBridge rules |
| `lambda:InvokeFunction`, `lambda:AddPermission` | Allow EventBridge to invoke Lambdas |
