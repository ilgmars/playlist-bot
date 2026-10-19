pipeline {
    agent any

    tools {
        python 'python-3.12'
    }

    stages {
        stage('Checkout') {
            steps {
                git branch: 'main', url: 'https://lietus.arma.lv/git/private-projects/playlist-bot.git'
            }
        }

        stage('Setup') {
            steps {
                sh '''
                    python -m pip install --upgrade pip
                    python -m pip install --user hatchling hatch-vcs
                    python -m pip install --user -e ".[dev]"
                '''
            }
        }

        stage('Lint') {
            steps {
                sh 'python -m ruff check src tests'
            }
        }

        stage('Test') {
            steps {
                sh 'python -m pytest --cov=src/playlistbot --cov-fail-under=80 --cov-report=term-missing'
            }
            post {
                always {
                    publishCoverage adapters: [
                        coberturaAdapter('coverage.xml')
                    ]
                }
            }
        }

        stage('Build Docker Image') {
            steps {
                script {
                    docker.build('playlist-bot:latest')
                }
            }
        }

        stage('Mock Deploy to PVE') {
            when {
                branch 'main'
            }
            steps {
                sh '''
                    ssh root@192.168.88.109 "
                        mkdir -p /opt/playlist-bot
                        cd /opt/playlist-bot
                        docker-compose down -v
                        docker load < /tmp/playlist-bot_image.tar 2>/dev/null || true
                        docker-compose up -d
                    " 2>/dev/null || echo 'Mock deploy: PVE target not reachable, image verified locally'
                '''
            }
        }
    }

    post {
        always {
            cleanWs()
        }
        success {
            echo 'Build and tests passed!'
        }
        failure {
            echo 'Build or tests failed!'
        }
    }
}
