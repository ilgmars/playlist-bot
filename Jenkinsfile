pipeline {
    agent { label 'validation' }

    options {
        timeout(time: 15, unit: 'MINUTES')
        disableConcurrentBuilds()
    }

    environment {
        TEST_IMAGE = "playlist-bot-test:${env.BUILD_NUMBER}"
        RUNTIME_IMAGE = "playlist-bot:latest"
    }

    stages {
        stage('Build Images') {
            steps {
                sh '''
                    docker build --target test -t "${TEST_IMAGE}" .
                    docker build --target runtime -t "${RUNTIME_IMAGE}" .
                '''
            }
        }

        stage('Lint') {
            steps {
                sh '''
                    docker run --rm --entrypoint python "${TEST_IMAGE}" -m ruff check src tests
                    docker run --rm --entrypoint python "${TEST_IMAGE}" -m ruff format --check src tests
                '''
            }
        }

        stage('Test') {
            steps {
                sh '''
                    docker run --rm --entrypoint python "${TEST_IMAGE}" \
                        -m pytest --cov=src/playlistbot --cov-fail-under=80 --cov-report=term-missing -q
                '''
            }
        }

        stage('Security Scan') {
            steps {
                sh '''
                    docker run --rm --entrypoint bandit "${TEST_IMAGE}" -r src -ll
                '''
            }
        }

        stage('Code Review (Automated)') {
            steps {
                sh '''
                    echo "=== TODOs/FIXMEs ==="
                    grep -rn -E "TODO|FIXME|XXX" src --include="*.py" || true

                    echo "=== Print statements ==="
                    grep -rn "print(" src --include="*.py" | grep -v "test_" || true

                    echo "=== Large functions (>50 lines) ==="
                    docker run --rm --entrypoint python "${TEST_IMAGE}" -c "
import ast, glob, sys
bad = []
for file in glob.glob('src/**/*.py', recursive=True):
    with open(file) as f:
        tree = ast.parse(f.read(), filename=file)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lines = node.end_lineno - node.lineno
            if lines > 50:
                bad.append(f'{file}:{node.lineno} {node.name} - {lines} lines')
if bad:
    for item in bad:
        print(item)
    sys.exit(1)
"
                '''
            }
        }

        stage('Verify Runtime Container') {
            steps {
                sh '''
                    docker run --rm "${RUNTIME_IMAGE}" --help
                '''
            }
        }

        stage('Mirror to GitHub') {
            when {
                branch 'main'
            }
            steps {
                withCredentials([string(credentialsId: 'github-token', variable: 'GITHUB_TOKEN')]) {
                    sh '''
                        set +x
                        git push --force "https://x-access-token:${GITHUB_TOKEN}@github.com/ilgmars/playlist-bot.git" HEAD:refs/heads/main
                    '''
                }
            }
        }
    }

    post {
        always {
            sh 'docker rmi "${TEST_IMAGE}" 2>/dev/null || true'
            cleanWs()
        }
        success {
            echo 'Build, tests, security scans, and review passed!'
        }
        failure {
            echo 'Build or tests failed!'
        }
    }
}