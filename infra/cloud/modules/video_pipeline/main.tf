locals {
  prefix = "${var.app_name}-${var.environment}"
}

# ── Networking ─────────────────────────────────────────────────────────────
# Duplicated (not centralized into a shared `network` module) default-VPC
# lookup -- see modules/aurora/main.tf's identical block. Only two call sites
# exist across this repo; extracting a shared module is premature at this
# scale and would force an unrelated refactor of the already-stable aurora
# module. Revisit if a third VPC-aware module appears.

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# Egress-only -- the task only makes outbound calls (S3, ECR, the vision-LLM
# API); nothing ever connects in. Mirrors modules/aurora's security group.
resource "aws_security_group" "video_task" {
  name        = "${local.prefix}-video-task"
  description = "Fargate video-analysis task security group (no ingress)"
  vpc_id      = data.aws_vpc.default.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ── ECS cluster + ECR repository ────────────────────────────────────────────
# Image build/push to this repo is out of scope for this pass (no CI exists
# yet per CLAUDE.md's "not yet built" list, and terraform apply hasn't run
# either) -- the task definition below references a `:latest` tag that won't
# exist until someone manually builds/pushes src/video_task's Dockerfile.

resource "aws_ecs_cluster" "video" {
  name = "${local.prefix}-video"
}

resource "aws_ecr_repository" "video_task" {
  name                 = "${local.prefix}-video-task"
  image_tag_mutability = "MUTABLE"
  force_delete         = true # dev-appropriate; revisit for a prod environment
}

# Placeholder for a real provider's API key -- value is never set by
# Terraform (no committed secret_version), populated out-of-band once a real
# provider exists, mirroring infra/local/.env's real-credential handling
# (keeping the real provider's own credentials separate from everything
# else).
resource "aws_secretsmanager_secret" "video_provider_api_key" {
  name                    = "${local.prefix}-video-provider-api-key"
  recovery_window_in_days = 0 # dev-appropriate; revisit for a prod environment
}

# ── ECS task execution role (pulls image, writes logs) ──────────────────────

resource "aws_iam_role" "video_task_execution" {
  name = "${local.prefix}-video-task-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "video_task_execution" {
  role       = aws_iam_role.video_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# ── ECS task role (the container's own AWS permissions) ─────────────────────

resource "aws_iam_role" "video_task" {
  name = "${local.prefix}-video-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "video_task" {
  name = "app-permissions"
  role = aws_iam_role.video_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadUploadObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${var.upload_bucket_arn}/*"
      },
      {
        Sid      = "WriteExtractedText"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${var.upload_bucket_arn}/extracted/*"
      },
      {
        Sid      = "VideoProviderApiKey"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = aws_secretsmanager_secret.video_provider_api_key.arn
      },
    ]
  })
}

resource "aws_cloudwatch_log_group" "video_task" {
  name              = "/ecs/${local.prefix}-video-task"
  retention_in_days = 14
}

resource "aws_ecs_task_definition" "video_task" {
  family                   = "${local.prefix}-video-task"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.video_task_cpu
  memory                   = var.video_task_memory
  execution_role_arn       = aws_iam_role.video_task_execution.arn
  task_role_arn            = aws_iam_role.video_task.arn

  container_definitions = jsonencode([{
    name      = "video-task"
    image     = "${aws_ecr_repository.video_task.repository_url}:latest" # image build/push is out of scope for this pass -- see comment above
    essential = true
    environment = [
      { name = "VIDEO_PROVIDER", value = var.video_provider },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.video_task.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "video-task"
      }
    }
  }])
}

# ── Step Functions execution role ───────────────────────────────────────────

resource "aws_iam_role" "video_state_machine" {
  name = "${local.prefix}-video-state-machine"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "video_state_machine" {
  name = "app-permissions"
  role = aws_iam_role.video_state_machine.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "RunVideoTask"
        Effect   = "Allow"
        Action   = ["ecs:RunTask"]
        Resource = aws_ecs_task_definition.video_task.arn
        Condition = {
          ArnEquals = { "ecs:cluster" = aws_ecs_cluster.video.arn }
        }
      },
      {
        Sid      = "PassEcsRoles"
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [aws_iam_role.video_task_execution.arn, aws_iam_role.video_task.arn]
        Condition = {
          StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" }
        }
      },
      {
        Sid      = "InvokeVideoCompletion"
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = var.video_completion_function_arn
      },
      {
        # ASSUMPTION, verify against real AWS: this is the well-known
        # requirement for Step Functions' ecs:runTask.sync managed
        # integration, which internally registers/polls an EventBridge rule
        # to learn when the ECS task stops. Resource pattern/exact required
        # actions unverified against this account.
        Sid      = "EcsSyncManagedRule"
        Effect   = "Allow"
        Action   = ["events:PutTargets", "events:PutRule", "events:DescribeRule"]
        Resource = "arn:aws:events:${var.aws_region}:*:rule/StepFunctionsGetEventsForECSTaskRule"
      },
    ]
  })
}

# ── Step Functions state machine ────────────────────────────────────────────
# STANDARD, not EXPRESS: Express workflows cap at 5 minutes and use
# at-least-once (not exactly-once) semantics -- unsuitable for a potentially
# minutes-long vision-LLM call and for StartExecution's name-based
# idempotency (see common/video.start_video_analysis_execution).
#
# ecs:runTask.sync (not waitForTaskToken): Step Functions' native ECS
# RunTask.sync integration already waits for the task to stop at zero
# incremental compute cost (AWS manages the poll internally), as long as the
# Fargate task itself does the full blocking work and reports status purely
# via exit code -- no callback-token plumbing needed. See src/video_task/main.py.
#
# ASSUMPTION, verify against real AWS: whether ecs:runTask.sync fails the
# state (triggering Catch below) on a nonzero container exit code specifically,
# vs. only on ECS-level stop reasons (task provisioning failure, etc).

resource "aws_sfn_state_machine" "video_analysis" {
  # This exact name is depended on by modules/lambda/main.tf's
  # local.video_state_machine_arn, which hand-constructs this state
  # machine's ARN rather than referencing this module's output directly (to
  # avoid a module cycle, since this module depends on that module's
  # video_completion function ARN). Keep the two in sync if this ever changes.
  name     = "${local.prefix}-video-analysis"
  role_arn = aws_iam_role.video_state_machine.arn
  type     = "STANDARD"

  definition = jsonencode({
    Comment = "Runs a Fargate task that downloads a video, calls VIDEO_PROVIDER, stages a JSONL result, and reports success/failure to video_completion."
    StartAt = "RunVideoAnalysisTask"
    States = {
      RunVideoAnalysisTask = {
        Type           = "Task"
        Resource       = "arn:aws:states:::ecs:runTask.sync"
        TimeoutSeconds = var.video_analysis_timeout_seconds
        Parameters = {
          Cluster        = aws_ecs_cluster.video.arn
          TaskDefinition = aws_ecs_task_definition.video_task.arn
          LaunchType     = "FARGATE"
          NetworkConfiguration = {
            AwsvpcConfiguration = {
              Subnets        = data.aws_subnets.default.ids
              SecurityGroups = [aws_security_group.video_task.id]
              AssignPublicIp = "ENABLED" # default-VPC public subnets, no NAT gateway in this repo
            }
          }
          Overrides = {
            ContainerOverrides = [{
              Name = "video-task"
              Environment = [
                { "Name" : "FILE_ID", "Value.$" : "$.file_id" },
                { "Name" : "S3_BUCKET", "Value.$" : "$.s3_bucket" },
              ]
            }]
          }
        }
        Catch = [{
          ErrorEquals = ["States.ALL"]
          ResultPath  = "$.error"
          Next        = "InvokeVideoCompletionFailed"
        }]
        ResultPath = "$.taskResult"
        Next       = "InvokeVideoCompletionSucceeded"
      }
      InvokeVideoCompletionSucceeded = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = var.video_completion_function_arn
          Payload = {
            "file_id.$"   = "$.file_id"
            "s3_bucket.$" = "$.s3_bucket"
            "status"      = "SUCCEEDED"
          }
        }
        End = true
      }
      InvokeVideoCompletionFailed = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = var.video_completion_function_arn
          Payload = {
            "file_id.$"   = "$.file_id"
            "s3_bucket.$" = "$.s3_bucket"
            "status"      = "FAILED"
            "error.$"     = "$.error"
          }
        }
        End = true
      }
    }
  })
}
