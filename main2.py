#importing every package that is needed
import discord
import json
import os
import openai
import rich
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from themoviedb import TMDb

# Initialize TMDb with API key
tmdb = TMDb(key="insert key here", language="en-US", region="US")
#import credentials file for mongodb, discord, openai, and tmdb as well as test connection to MongoDB
console = rich.get_console()
creds = json.load(open('creds.json'))

mongodb_client = MongoClient(creds['mongodb']['url'], server_api=ServerApi('1'))
try:
    mongodb_client.admin.command('ping')
    print("Pinged your deployment. You successfully connected to MongoDB!")
except Exception as e:
    print(e)

openai_client = openai.OpenAI(api_key=creds['openai']['api_key'])
db = mongodb_client['movietrack']
collection = db['recommendations']
#initialize openai system prompts
def ask_openai(sys_prompt, user_prompt, return_json=False):
    console.print(f"[bold]System prompt:[/bold] {sys_prompt}")
    console.print(f"[bold]User prompt:[/bold] {user_prompt}")
    chat_completion = openai_client.chat.completions.create(
            messages=[{"role": "system","content": sys_prompt},
        {"role": "user", "content": user_prompt}
        ],
        model="gpt-4-turbo-preview",
        response_format=({"type": "json_object"} if return_json else None)
    )
    response = chat_completion.choices[0].message.content
    console.print(f"[bold]Response:[/bold] {response}")
    return response

#documentation to help ChatGPT
sys_prompt_search = """
You are a tool that can create movie search queries.

Interpret the user's prompt and generate a search query that can be used to search for the user's desired movie.

Use the following documentation to help you:

---

You can narrow down your search using field filters. The available filters include title, overview, release date, popularity, vote average, genre id.


If someone asks for recommendations, you can search through the database for movies that match the user's preferences and provide them with a list of recommendations, as well as the reasons why you chose them and additional information like genre, release date, and actors. If possible, provide a link to the movie's page on the database and/or what streaming services it is available on. If you can't find any movies that match the user's preferences, let them know. You can also recommend something to another user based on their preferences and that user can see any recommednations.
---

Examples of search queries:

User query: "Find me the movie Jurassic Park."
Search query: "Jurassic Park"

User query: "Find me an action movie."
Search query: "action"

User query: "I want to watch a movie with Tom Hanks"
Search query: "Tom Hanks"

VERY IMPORTANT: Return the search query as a string WITHOUT the quotes. RETURN ONLY THE SEARCH STRING.
"""

sys_prompt_response = """
Interpret search results from movie database based on the user's query.

Add suggestions for follow up searches.
"""
sys_prompt_recognize_action = """
You are a bot that recognizes the action a user wants to take based on their prompt in Discord.

There are three possible actions a user can take: recommend a movie to a user, get recommendations that have been, or search database for information.
Recommend should list action, movie, director, recipient, actors, genre, release date, streaming service, reason for recommending, and the recommender

Return JSON. Example formats are given below.

Examples of user prompts and responses:

---

From: joe
User prompt: I recommend the movie The Godfather to @jane because she loves crime movies
Response: {"action": "recommend", "movie": "The Godfather ", "director": "Francis Ford Coppola", "recipient": "jane", "actors": "Al Pacino and Marlon Brando", "genre": "crime", "release date": "March 24, 1972", "streaming service": "Paramount+",  "reason": "loves crime movies", "recommender": "joe"}

---
To: jane
From: joe
User prompt: rec The Godfather 4 @jane because she loves crime movies
Response: see above

---

From: jane
User prompt: what did @joe recommend to me?
Response: {"action": "get_recommendations", "recipient": "jane", "recommender": "joe"}

---

From: jane
User prompt: rec @joe for me
Response: {"action": "get_recommendations", "recipient": "jane", "recommender": "joe"}

---

From: jane
User prompt: my recs
Response: {"action": "get_recommendations", "recipient": "jane"}

---

From: jane
User prompt: rec @joe @jane
Response: {"action": "get_recommendations", "recipient": "jane", "recommender": "joe"}

---

From: jane
User prompt: what did I recommend?
Response: {"action": "get_recommendations", "recommender": "jane"}

---

From: jane
User prompt: who recommended The Godfather?
Response: {"action": "get_recommendations", "movie": "The Godfather"}

---

From: bob
User prompt: Name a movie with Tom Hanks
Response: {"action": "search_movies", "query": "actor: Tom Hanks"}
"""

#function to search for movies and post the responses here
def search_movies(user_prompt):
    search_string = ask_openai(sys_prompt_search, user_prompt)
    print("Generated Search Query:", search_string)
    print("OpenAI Response:", search_string)
    movies_results = tmdb.search().movies(search_string)
    print("TMDB API Response:", movies_results)
    console.rule("tmdb results")
    console.print(movies_results)

    # Check if there are any movie items in the search results
    if len(movies_results) == 0:
        console.print("No movies found for the search query.")
        return []

    # Extract relevant movie information from the API response
    movies = []
    for movie in movies_results[:5]:
        movie_details = {
            "title": movie.title,
            "overview": movie.overview,
            "release_date": movie.release_date,
            "poster_path": movie.poster_path,
            "popularity": movie.popularity,
            "vote_average": movie.vote_average,
            "genre_ids": movie.genre_ids, 
        }
        movies.append(movie_details)
    return movies

# recognize who sent the message, what the prompt is, and what action to take
def recognize_action(from_user, user_prompt):
    action = ask_openai(sys_prompt_recognize_action, f"From: {from_user}\nUser prompt: {user_prompt}", True)
    return json.loads(action)

#save recommendation using set conditions and uploading it to MongoDB
def save_recommendation(action):
    try:
        if 'recommender' not in action or 'recipient' not in action:
            raise ValueError("Missing recommender or recipient in action.")
        if 'movie' not in action:
            raise ValueError("Missing movie_title in action.")

        recommendation = {
            "recommender": action['recommender'],
            "recipient": action['recipient'],
            "movie": action['movie'],
            "director": action.get('director'),
            "actors": action.get('actors'),
            "genre": action.get('genre'),
            "release date": action.get('release date'),
            "streaming service": action.get('streaming service'),
            "reason": action.get('reason')
        }
        mongodb_client.movietrack.recommendations.insert_one(recommendation)
        return True
    except Exception as e:
        print(f'Error saving recommendation: {e}')
        return False

#print recommendations when asked to retreive them
def get_recommendations(action):
    if 'recommender' in action and 'recipient' in action:
        recommendations = list(mongodb_client.movietrack.recommendations.find({"recommender": action['recommender'], "recipient": action['recipient']}))
    elif 'recommender' in action:
        recommendations = list(mongodb_client.movietrack.recommendations.find({"recommender": action['recommender']}))
    elif 'recipient' in action:
        recommendations = list(mongodb_client.movietrack.recommendations.find({"recipient": action['recipient']}))
    else:
        recommendations = []
    return recommendations

#set up discord permissions
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

#log discord bot into server
@client.event
async def on_ready():
    print(f'We have logged in as {client.user}')

# function for what messages should do
@client.event
async def on_message(message):
    if message.author == client.user:
        return
#mo calls the bot
    if message.content.startswith('/mo'):
        action = recognize_action(message.author.name, message.content[4:])
#save recommendation to database
        if action['action'] == 'recommend':
            result = save_recommendation(action)
            if result:
                await message.channel.send("I saved your recommendation.")
            else:
                await message.channel.send("I'm sorry, I couldn't save your recommendation.")
#print recommendations previously saved
        elif action['action'] == 'get_recommendations':
            recommendations = get_recommendations(action)
            if len(recommendations) == 0:
                await message.channel.send("I'm sorry, I couldn't find any recommendations.")
            else:
                for recommendation in recommendations:
                    await message.channel.send(recommendation)
# search for movies based on condition given and print these out
        elif action['action'] == 'search_movies':
            movies = search_movies(action['query'])  # Search for movies based on the query
            if movies:
                for movie in movies:
                    await message.channel.send(f"Title: {movie['title']}")
                    await message.channel.send(f"Overview: {movie['overview']}")
                    await message.channel.send(f"Release Date: {movie['release_date']}")
                    await message.channel.send(f"Poster: {movie['poster_path']}")
                    await message.channel.send(f"Popularity: {movie['popularity']}")
                    await message.channel.send(f"Vote Average: {movie['vote_average']}")
                    await message.channel.send(f"Genres: {movie['genre_ids']}")
                    #await message.channel.send("\n")
            else:
                await message.channel.send("I couldn't find any movies matching your search.")

        else:
            await message.channel.send(action)
#use discord credentials to login
client.run(creds['discord_token'])
