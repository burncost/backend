import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import PyMongoError
# from . app.config import settings

# print(settings.MONGODB_URL)

async def test_mongo_connection():
    uri = f"mongodb://localhost:27017/"
    client = AsyncIOMotorClient(uri)

    db = client['burncostdb']
    try:
        await client.admin.command('ping')
        print(f'Connection Successful! {uri}')

        collections = await db.list_collection_names()
        print(f'Collections in BurncostDB: {collections}')
    except PyMongoError as e:
        print('MongoDB Error: {e}')
    finally:
        client.close()
        
if __name__=="__main__":
    asyncio.run(test_mongo_connection())
