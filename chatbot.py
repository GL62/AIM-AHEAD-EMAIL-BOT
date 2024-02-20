import os
import time
import csv
import sqlite3
import pickle
import json
import openai
import sys
import smtplib, ssl
import time
import imaplib
import email
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from openai import OpenAI

class PyVizProAssistant:
    def __init__(self, openai_api_key, organization_key, db_name, faq_file_path = None):
        os.environ['SomeKey'] = openai_api_key
        os.environ['GPTORG'] = organization_key
        self.client = OpenAI(organization = organization_key, api_key=openai_api_key)
        self.faq_file_id = self.upload_faq_file(faq_file_path)
        self.conn = sqlite3.connect(db_name)
        self.cursor = self.conn.cursor()
        self.create_table()

    def upload_faq_file(self, faq_file_path):
        if faq_file_path != None:
          file = self.client.files.create(file=open(faq_file_path, "rb"), purpose='assistants')
          faq_file_id = file.id
        else:
          faq_file_id = None
        return faq_file_id

    def create_assistant(self, name, instructions, model="gpt-4-1106-preview"):
        self.assistant = self.client.beta.assistants.create(
            name=name,
            instructions=instructions,
            model=model,
            tools=[
                {"type": "retrieval"},
                {"type": "code_interpreter"},
            ],
            file_ids=[self.faq_file_id]               #if faq file not needed comment out
        )

    def store_current_convo_in_csv(self, questions_response_dict):
        file_name = "current_conversation"
        directory_path = '.'
        file_path = os.path.join(directory_path, file_name)
        file_exists = os.path.exists(file_name)
        os.makedirs(directory_path, exist_ok=True)

        with open(file_name, mode='a', newline='') as file:
            writer = csv.writer(file)

            if not file_exists:
              writer.writerow(["Question", "Answers"])

            for question, answers in questions_response_dict.items():
                writer.writerow([question, answers])
        # print(f"Question-answer pairs have been written to '{file_name}'.")

    def get_links(self, gpt_response, user_question):
        websites_to_omit = {"https://study.com"}
        resources_links = []
        try:
            from googlesearch import search
        except ImportError:
            print("No module named 'google' found")
            return []

        for link in search(user_question, num=5, stop=10, pause=5):
            curr_link = link.split(".com")
            if f"{curr_link[0]}.com" not in websites_to_omit:
                resources_links.append(link)

        for link in search(gpt_response, num=5, stop=10, pause=5):
            curr_link = link.split(".com")
            if f"{curr_link[0]}.com" not in websites_to_omit:
                resources_links.append(link)

        return resources_links

    def create_table(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_threads (
                user_id TEXT PRIMARY KEY,
                thread BLOB
            )
        ''')
        self.conn.commit()

    def store_user_thread(self, user_id, thread):
        serialized_thread = pickle.dumps(thread)
        print(type(user_id))
        print("\n")
        print(type(thread))
        print("\n")
        print(type(serialized_thread))
        self.cursor.execute('INSERT OR REPLACE INTO user_threads (user_id, thread) VALUES (?, ?)',
                            (user_id, sqlite3.Binary(serialized_thread)))
        self.conn.commit()

    def retrieve_user_thread(self, user_id):
        self.cursor.execute('SELECT thread FROM user_threads WHERE user_id = ?', (user_id,))
        result = self.cursor.fetchone()
        if result:
            serialized_thread = result[0]
            return pickle.loads(serialized_thread)
        else:
            return None  # Handle the case where the user_id is not found


    def return_assistant_response(self, user_question, assistant_message, message_response_dict):
        if "beyond my expertise" not in assistant_message:
            resources_links = self.get_links(assistant_message, user_question)
            if resources_links:
                links_string = "\n".join(resources_links)
                assistant_message_with_links = f"{assistant_message}\n\nHere are some resources you can explore further:\n{links_string}"
                message_response_dict[user_question] = assistant_message_with_links
                return assistant_message_with_links
        else:
            return "Please note, I specialize in Python data visualization. Questions outside this realm is beyond my expertise"

    def get_assistant_response(self, thread, run):
        conversation = self.client.beta.threads.messages.list(thread_id=thread.id)
        messages_present = next(
            (msg for msg in reversed(conversation.data) if msg.run_id == run.id and msg.role == "assistant"),
            None
        )

        if messages_present:
            assistant_message = messages_present.content[0].text.value
        else:
            print("No message found from the assistant in this run.")
        return assistant_message, messages_present

    def start_chatbot(self, curr_user, user_question):
        print("running started")
        retrieved_thread = self.retrieve_user_thread(curr_user)
        if retrieved_thread:
            thread = retrieved_thread
            print("Old thread present for this user")
        else:
            print("New thread created")
            thread = self.client.beta.threads.create()
            self.store_user_thread(curr_user, thread)

        message_response_dict = {}

        message = self.client.beta.threads.messages.create(
            thread_id=thread.id,
            role="user",
            content=user_question
        )

        run = self.client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=self.assistant.id
        )

        try_count = 0
        while run.status not in ["completed", "failed", "requires_action"]:
            print("...", end="")
            if try_count > 0:
                time.sleep(5)

            run = self.client.beta.threads.runs.retrieve(
                thread_id=thread.id,
                run_id=run.id
            )
            try_count += 1

        if run.status == "Failed":
            return "An error has occurred with the API"

        assistant_message, messages_present = self.get_assistant_response(thread, run)
        assistant_response = self.return_assistant_response(user_question, assistant_message, message_response_dict)
        self.store_current_convo_in_csv(message_response_dict)
        return assistant_response


class mail_bot:
    def __init__(self, api_key, chatbot_instruction, sender, mail_host, password, sender_email, sent_folder, inbox_folder, IMAP_SSL, SMTP_SSL, organization_key, db_name, faq_file_path = None):
        self.api_key = api_key
        self.chatbot_instruction = chatbot_instruction
        self.sender = sender
        self.mail_host = mail_host
        self.password = password
        self.sender_email = sender_email
        self.sent_folder = sent_folder
        self.inbox_folder = inbox_folder
        self.IMAP_SSL = IMAP_SSL
        self.SMTP_SSL = SMTP_SSL
        self.organization_key = organization_key
        self.faq_file_path = faq_file_path
        self.db_name = db_name

    def reply_to_emails(self, unread_messages: bool):
        # Unread mails
        unread_mails = [[], [], [], []]

        # Connect to the mailbox
        mail = imaplib.IMAP4_SSL(self.mail_host)
        mail.login(self.sender_email, self.password)
        mail.select(self.inbox_folder)

        # Search for unread emails in the mailbox
        status, messages = mail.search(None, 'UNSEEN' if unread_messages else 'ALL')
        message_ids = messages[0].split()

        for msg_id in message_ids:
            # Fetch the email
            status, data = mail.fetch(msg_id, '(RFC822)')
            raw_email = data[0][1]
            email_message = email.message_from_bytes(raw_email)

            # Extract email details
            sender = email_message['From']
            old_subject = email_message['Subject']
            parsing_subject = old_subject.split(':')[-1]
            subject = f'REPLY: {parsing_subject}'
            date = email_message['Date']
            #Get the email body content
            body = self.get_email_body(email_message, old_subject)
            body_str = str(body, encoding='utf-8')
            # Generate answer with gpt
            # print('we are here')
            assistant_answer = self.gpt_init(sender, body_str)
            # assistant_answer = body_str
            # Send email
            mail_operator.send_email(subject=subject, body=assistant_answer, receiver_email=sender)

        mail.logout()
        return unread_mails

    def get_email_body(self, email_message, subject):
        body = ""

        if email_message.is_multipart():
            for part in email_message.walk():
                content = part.get_content_type()
                disposition = str(part.get('Content-Disposition'))
                if content == 'text/plain' and 'attachment' not in disposition:
                    body = part.get_payload(decode=True)
                    break
        else:
            body = email_message.get_payload(decode=True)

        if body is 'ESCALATE':
          self.escalate(subject)

        return body

    def send_email(self, subject, body, receiver_email):
        # Create a multipart message and set headers
        message = MIMEMultipart()
        message["From"] = self.sender_email
        message["To"] = receiver_email
        message["Subject"] = subject

        # Add body to email
        message.attach(MIMEText(str(body), "plain"))

        # Add attachment to message and convert message to string
        text = message.as_string()

        # Log in to server using secure context and send email
        context = ssl.create_default_context()
        with smtplib.SMTP(self.mail_host, 587) as server:
            server.starttls(context=context)
            server.login(self.sender_email, self.password)
            server.sendmail(self.sender_email, receiver_email, text)

        # Add email to send folder, and mark email as seen
        imap = imaplib.IMAP4_SSL(self.mail_host, self.IMAP_SSL)
        imap.login(self.sender_email, self.password)
        imap.append(self.sent_folder, '\\Seen', imaplib.Time2Internaldate(time.time()), text.encode('utf8'))
        imap.logout()

    def gpt_init(self, sender, body):
      assistant = PyVizProAssistant(self.api_key, self.organization_key, self.db_name, self.faq_file_path)
      name = "PyVizPro"
      assistant.create_assistant(name, self.chatbot_instruction)
      new_body = assistant.start_chatbot(sender, body)
      return new_body

    def escalate(self, email_subject):
      emails = [
          'jennifer.hayes@bison.howard.edu',
          'ahoyal.dbmi@gmail.com',
          'saharsha.tiwari@bison.howard.edu',
          'ujjawal.shah@bison.howard.edu',
          'sariah.adams@bison.howard.edu',
          'hrishav.sapkota@bison.howard.edu',
          'sameer.acharya@bison.howard.edu',
          'julian.matthews@bison.howard.edu',
          'amir.ince@bison.howard.edu',
          'howard.prioleau@bison.howard.edu',
          'sijan.shrestha@bison.howard.edu',
          'saujanya.thapaliya1@bison.howard.edu',
          'william.edwards@bison.howard.edu',
          'bryan.mildort@bison.howard.edu',
          'sajan.acharya@bison.howard.edu',
          'donaldechefu@gmail.com',
          'oluwafemi.oladosu@bison.howard.edu',
          'suprabhat.rijal@bison.howard.edu',
          'johnny.carterjr@gmail.com',
          'guy.lingani@bison.howard.edu'
      ]
      subject = email_subject.split('(')[0]
      for email in emails:
        self.send_email(f'{subject} needs further assistance', body, email)




    # def ai_responder(self, message):
    #     # print("Reached AI responder function")
    #     openai.api_key = self.api_key
    #     response = openai.chat.completions.create(
    #     model="gpt-3.5-turbo-0301",
    #     messages=[
    #         {
    #             "role": "system",
    #             "content": self.api_role
    #         },
    #         {
    #             "role": "user",
    #             "content": str(message)
    #         }
    #     ],
    #     temperature=0.8,
    #     max_tokens=256
    #     )

    #     # Return generated message
    #     my_openai_obj = list(response.choices)[0]
    #     return (my_openai_obj.to_dict()['message']['content'])



# if __name__ == "__main__":

    # # Private Variables (replace this with your data)
    # # new_api_key = "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"
    # new_api_key = os.environ['SomeKey']
    # organization_key = os.environ['GPTORG']
    # new_mail_host = "smtp-mail.outlook.com"
    # new_password = "Gaat2024"
    # new_your_email = "guyaimaheadtest@outlook.com"

    # # GPT variables
    # new_api_role = "You are a service assistant with expertise in chatGPT usage."

    # # Email Variables
    # new_subject = "generated answer with GPT BOT"
    # new_sender = "GPT BOT"
    # new_sent_folder = 'SENT'
    # new_inbox_folder = 'INBOX'
    # new_SMTP_SSL = 587
    # new_IMAP_SSL = 993
    # file_path = "question_answers.csv"
    # db_name = "user_thread_store.db"
    # # Instance of bot class
    # mail_operator = mail_bot(
    #     new_api_key,
    #     new_api_role,
    #     new_sender,
    #     new_mail_host,
    #     new_password,
    #     new_your_email,
    #     new_sent_folder,
    #     new_inbox_folder,
    #     new_IMAP_SSL,
    #     new_SMTP_SSL
    # )

    # # Here we read all messages
    # unread_messages = mail_operator.reply_to_emails(unread_messages=True)

    # print(unread_messages, "done")
# Private Variables (replace this with your data)
# new_api_key = "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"
from google.colab import userdata


new_api_key = userdata.get('SomeKey')
organization_key = userdata.get('GPTORG')
# new_mail_host = "smtp-mail.outlook.com"
# new_mail_host = "smtp.aim-ahead.net"
# new_password = "dstc@HU1867"
# new_your_email = "helpdesk-bot@aim-ahead.net"
new_mail_host = "smtp-mail.outlook.com"
new_password = "Qw3r2024#"
new_your_email = "donaldaimaheadtest@outlook.com"

# GPT variables
# new_api_role = "You are a service assistant with expertise in chatGPT usage."
chatbot_instruction = """ You are PyVizPro, a data visualization, Python and other programming languages expert with a strong command of Python. You've spent years honing your data visualization skills, and you're ready to help others understand how to create stunning visuals from their data. You use examples from well-known data visualization libraries like Matplotlib, Seaborn, Plotly and provide code snippets and resources to help illustrate your points.
Your language must be easy to grasp for someone new to data visualization in Python, and you'll use a mix of relatable analogies and everyday language to make your explanations engaging.
If you encounter a question you can't answer, don't make things up; instead, ask for more details to provide the best answer.
If the user asks questions not related to programming, data visualization, ml and other programming languages, you must not answer that question and inform the user that "Please note, I specialize in Python data visualization. Questions outside this realm is beyond my expertise."
if the user wants help with the code or ask any programming question, you must write the respond with the code as well in the answer.
"""
chatbot_instruction_new = """ You are AIProgHelper, a programming expert specializing in programming, data management, data science, and machine learning with strong command in all the programming languages.
You've spent years honing your programming, data visualization, and ML skills, and you're ready to help others resolve any issues they have. You use examples from well-known libraries, documentation and provide code snippets and resources to help illustrate your points.
Your language must be easy to grasp for someone new to programming, and you'll use a mix of relatable analogies and everyday language to make your explanations engaging.
If you encounter a question you can't answer, don't make things up; instead, ask for more details to provide the best answer.
If the user asks questions not related to programming, data visualization, ml and other programming languages, you must not answer that question and inform the user that "Please note, I specialize in Python data visualization. Questions outside this realm is beyond my expertise."
If the user wants help with the code or ask any programming question, you must write the respond with the code as well in the answer.
"""

faq_file_path = "faq.csv"
db_name = "user_thread_store.db"
# assistant.create_assistant("PyVizPro", prompt)
# current_user_id = "donaldechefu@gmail.com"
# current_question = "How to code and extract the phenotype in HAIL in Python in Juypter to start the GWAS from VCF files."
# new_body = assistant.start_chatbot(current_user_id, current_question)

# Email Variables
new_subject = "generated answer with GPT BOT"
new_sender = "GPT BOT"
new_sent_folder = 'SENT'
new_inbox_folder = 'INBOX'
new_SMTP_SSL = 587
new_IMAP_SSL = 993
# Instance of bot class
mail_operator = mail_bot(
    new_api_key,
    chatbot_instruction,
    new_sender,
    new_mail_host,
    new_password,
    new_your_email,
    new_sent_folder,
    new_inbox_folder,
    new_IMAP_SSL,
    new_SMTP_SSL,
    organization_key,
    db_name,
    faq_file_path
)

# Here we read all messages
unread_messages = mail_operator.reply_to_emails(unread_messages=True)

print(unread_messages, "done")
