# Native Lambda Python runtime, matching the pattern used by the sibling
# predicted-conditions repo (see its api/Dockerfile) — the Lambda Runtime
# Interface Client calls api.main.handler directly for every invocation,
# no HTTP web-adapter translation layer in between.
FROM public.ecr.aws/lambda/python:3.12

WORKDIR ${LAMBDA_TASK_ROOT}

# Install dependencies first for layer caching
COPY api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code.
COPY ocr_auditor/ ./ocr_auditor/
COPY api/ ./api/

CMD ["api.main.handler"]
