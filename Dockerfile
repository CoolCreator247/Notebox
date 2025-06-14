# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set environment variables
ENV PYTHONUNBUFFERED 1
ENV APP_HOME /app
ENV PORT 8080

# Create and set the working directory
WORKDIR $APP_HOME

# Install dependencies individually
RUN pip install --no-cache-dir 'Flask>=2.0'
RUN pip install --no-cache-dir 'requests>=2.20'
RUN pip install --no-cache-dir 'openai' # Added for OpenAI SDK
RUN pip install --no-cache-dir 'gunicorn>=20.0'
RUN pip install --no-cache-dir 'python-dotenv'

# Copy the rest of the application code into the container
COPY . .

# Make port 8080 available to the world outside this container
EXPOSE 8080

# Define environment variables for API keys if they are to be set at runtime by Fly.io
# These are placeholders; actual values should be set as secrets in Fly.io
# ENV DEEPSEEK_API_KEY YOUR_DEEPSEEK_API_KEY_HERE
# ENV DEEPGRAM_API_KEY YOUR_DEEPGRAM_API_KEY_HERE
# The app_fly.py will use its hardcoded fallbacks if these are not set.

# Run app_fly.py when the container launches
CMD gunicorn --bind 0.0.0.0:$PORT --timeout 60 app_fly:app
