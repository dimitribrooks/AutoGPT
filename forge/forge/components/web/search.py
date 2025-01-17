import json
import logging
import time
from typing import Iterator, Optional

from duckduckgo_search import DDGS
from pydantic import BaseModel, SecretStr

from forge.agent.components import ConfigurableComponent
from forge.agent.protocols import CommandProvider, DirectiveProvider
from forge.command import Command, command
from forge.models.config import UserConfigurable
from forge.models.json_schema import JSONSchema
from forge.utils.exceptions import ConfigurationError

logger = logging.getLogger(__name__)


class WebSearchConfiguration(BaseModel):
    google_api_key: Optional[SecretStr] = UserConfigurable(
        None, from_env="GOOGLE_API_KEY", exclude=True
    )
    google_custom_search_engine_id: Optional[SecretStr] = UserConfigurable(
        None, from_env="GOOGLE_CUSTOM_SEARCH_ENGINE_ID", exclude=True
    )
    duckduckgo_max_attempts: int = 3


class WebSearchComponent(
    DirectiveProvider, CommandProvider, ConfigurableComponent[WebSearchConfiguration]
):
    """Provides commands to search the web."""

    config_class = WebSearchConfiguration

    def __init__(self, config: Optional[WebSearchConfiguration] = None):
        ConfigurableComponent.__init__(self, config)

        if (
            not self.config.google_api_key
            or not self.config.google_custom_search_engine_id
        ):
            logger.info(
                "Configure google_api_key and custom_search_engine_id "
                "to use Google API search."
            )

    def get_resources(self) -> Iterator[str]:
        yield "Internet access for searches and information gathering."

    def get_commands(self) -> Iterator[Command]:
        yield self.web_search

        if self.config.google_api_key and self.config.google_custom_search_engine_id:
            yield self.google

    @command(
        ["web_search", "search"],
        "Searches the web",
        {
            "query": JSONSchema(
                type=JSONSchema.Type.STRING,
                description="The search query",
                required=True,
            ),
            "num_results": JSONSchema(
                type=JSONSchema.Type.INTEGER,
                description="The number of results to return",
                minimum=1,
                maximum=10,
                required=False,
            ),
        },
    )
    def web_search(self, query: str, num_results: int = 8) -> str:
        """Return the results of a Google search

        Args:
            query (str): The search query.
            num_results (int): The number of results to return.

        Returns:
            str: The results of the search.
        """
        search_results = []
        attempts = 0

        while attempts < self.config.duckduckgo_max_attempts:
            if not query:
                return json.dumps(search_results)

            search_results = DDGS().text(query, max_results=num_results)

            if search_results:
                break

            time.sleep(1)
            attempts += 1

        search_results = [
            {
                "title": r["title"],
                "url": r["href"],
                **({"exerpt": r["body"]} if r.get("body") else {}),
            }
            for r in search_results
        ]

        results = ("## Search results\n") + "\n\n".join(
            f"### \"{r['title']}\"\n"
            f"**URL:** {r['url']}  \n"
            "**Excerpt:** " + (f'"{exerpt}"' if (exerpt := r.get("exerpt")) else "N/A")
            for r in search_results
        )
        return self.safe_google_results(results)

    @command(
        ["google"],
        "Google Search",
        {
            "query": JSONSchema(
                type=JSONSchema.Type.STRING,
                description="The search query",
                required=True,
            ),
            "num_results": JSONSchema(
                type=JSONSchema.Type.INTEGER,
                description="The number of results to return",
                minimum=1,
                maximum=10,
                required=False,
            ),
        },
    )
    def google(self, query: str, num_results: int = 8) -> str | list[str]:
        """Return the results of a Google search using the official Google API

        Args:
            query (str): The search query.
            num_results (int): The number of results to return.

        Returns:
            str: The results of the search.
        """

        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError

        try:
            # Should be the case if this command is enabled:
            assert self.config.google_api_key
            assert self.config.google_custom_search_engine_id

            # Initialize the Custom Search API service
            service = build(
                "customsearch",
                "v1",
                developerKey=self.config.google_api_key.get_secret_value(),
            )

            # Send the search query and retrieve the results
            result = (
                service.cse()
                .list(
                    q=query,
                    cx=self.config.google_custom_search_engine_id.get_secret_value(),
                    num=num_results,
                )
                .execute()
            )

            # Extract the search result items from the response
            search_results = result.get("items", [])

            # Create a list of only the URLs from the search results
            search_results_links = [item["link"] for item in search_results]

        except HttpError as e:
            # Handle errors in the API call
            error_details = json.loads(e.content.decode())

            # Check if the error is related to an invalid or missing API key
            if error_details.get("error", {}).get(
                "code"
            ) == 403 and "invalid API key" in error_details.get("error", {}).get(
                "message", ""
            ):
                raise ConfigurationError(
                    "The provided Google API key is invalid or missing."
                )
            raise
        # google_result can be a list or a string depending on the search results

        # Return the list of search result URLs
        return self.safe_google_results(search_results_links)

    def safe_google_results(self, results: str | list) -> str:
        """
            Return the results of a Google search in a safe format.

        Args:
            results (str | list): The search results.

        Returns:
            str: The results of the search.
        """
        if isinstance(results, list):
            safe_message = json.dumps(
                [result.encode("utf-8", "ignore").decode("utf-8") for result in results]
            )
        else:
            safe_message = results.encode("utf-8", "ignore").decode("utf-8")
        return safe_message
