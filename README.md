To Run:

MAKE A VENV<br>
INSTALL ALL REQUIREMENTS<br>
pip install -r requirements.txt<br>

Open Docker Desktop<br>
docker start rabbitmq<br>
python manager.py<br>
python test_submit.py in different terminal<br>

You can go to http://localhost:15672/ to see the queue working<br>
Username and password are 'guest'

<br>

Ctrl+C to stop the manager