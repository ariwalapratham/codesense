"""MongoDB connection management with AsyncMongoClient."""

import logging

from pymongo import AsyncMongoClient

from config.settings import settings

logger = logging.getLogger(__name__)


class MongoDB:
    """MongoDB client with database connection."""

    def __init__(self):
        """Initialize MongoDB client and connect to database."""
        # Create AsyncMongoClient with configs
        self.client = AsyncMongoClient(
            settings.mongodb_url,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=10000,
            maxPoolSize=50,
            minPoolSize=5,
            retryWrites=True,
            retryReads=True,
            appName="CodeSense"
        )

        # Connect to database
        self.db = self.client[settings.db_name]

    async def close(self):
        """Close MongoDB connection."""
        await self.client.close()
        logger.info("MongoDB connection closed")

    def create_collection(self, name):
        """
        Get or create a collection by name.
        In MongoDB, collections are created implicitly when first used.
        This method just returns the collection object.
        """
        return self.db[name]


# Create MongoDB instance
mongodb = MongoDB()

csRepositories = mongodb.create_collection("csRepositories")
csChunks = mongodb.create_collection("csChunks")
