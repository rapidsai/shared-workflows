#!/usr/bin/env python3
"""
GitHub Project GraphQL Helper Script.

This script retrieves project field information from GitHub Projects using the GraphQL API.
It fetches custom fields, their IDs, and options for single-select fields, excluding
non-project fields.
"""

import requests
import json
import argparse
from typing import Dict, List, Tuple, Any
from pprint import pprint


def get_project_info(org: str, project_number: int, token: str) -> Tuple[str, Dict[str, Any]]:
    """
    Retrieve project information and custom fields from GitHub Projects.

    Args:
        org: GitHub organization name
        project_number: GitHub project number (integer)
        token: GitHub personal access token with appropriate permissions

    Returns:
        Tuple containing:
            - project_id: The GitHub project ID (string)
            - fields: Dictionary mapping field names to field configurations

    Raises:
        requests.RequestException: If the HTTP request fails
        KeyError: If the expected data structure is not found in the response
    """
    headers = {"Authorization": f"Bearer {token}"}

    query = '''
        query($org: String!, $number: Int!) {
            organization(login: $org){
            projectV2(number: $number) {
                id
            }
            }
        }
    '''

    variables = {
        "org": org,
        "number": int(project_number),
    }

    data = {
        "query": query,
        "variables": variables,
    }

    response = requests.post("https://api.github.com/graphql", headers=headers, json=data)
    response.raise_for_status()  # Raise exception for bad status codes
    response_json = json.loads(response.text)

    project_id = response_json['data']['organization']['projectV2']['id']

    query = '''
    query($node: ID!){
      node(id: $node) {
        ... on ProjectV2 {
          fields(first: 20) {
            nodes {
              ... on ProjectV2Field {
                id
                name
              }
              ... on ProjectV2IterationField {
                id
                name
                configuration {
                  iterations {
                    startDate
                    id
                  }
                }
              }
              ... on ProjectV2SingleSelectField {
                id
                name
                options {
                  id
                  name
                }
              }
            }
          }
        }
      }
    }
    '''

    variables = {
        "node": project_id,
    }

    data = {
        "query": query,
        "variables": variables,
    }

    fields_response = requests.post("https://api.github.com/graphql", headers=headers, json=data)
    fields_response.raise_for_status()  # Raise exception for bad status codes
    fields_response_json = json.loads(fields_response.text)

    # Standard GitHub project fields that should be excluded as they are not controlled by the projectv2 API
    not_project_fields = ['Title', 'Assignees', 'Labels', 'Linked pull requests', 'Reviewers', 'Repository', 'Milestone', 'Tracks']

    # Filter out standard project fields and create field mappings
    field_names = [
        {'name': field['name'], 'id': field['id']}
        for field in fields_response_json['data']['node']['fields']['nodes']
        if field['name'] not in not_project_fields
    ]

    fields = {
        field['name']: field
        for field in fields_response_json['data']['node']['fields']['nodes']
        if field['name'] not in not_project_fields
    }

    # Process options for single-select fields
    for field in fields.values():
        if 'options' in field:
            field['options'] = {option['name']: option['id'] for option in field['options']}

    return project_id, fields


def main() -> None:
    """
    Main function to parse command line arguments and execute the script.

    Parses command line arguments for GitHub organization, project number,
    and personal access token, then retrieves and displays project field information.
    """
    # Set up argument parser
    parser = argparse.ArgumentParser(description='GitHub Project GraphQL Helper')
    parser.add_argument('--token', '-t', required=True, help='GitHub personal access token')
    parser.add_argument('--org', '-o', required=True, help='GitHub organization name')
    parser.add_argument('--project', '-p', required=True, type=int, help='GitHub project number')

    args = parser.parse_args()

    try:
        # Use the provided arguments
        project_id, fields = get_project_info(org=args.org, project_number=args.project, token=args.token)

        pprint(project_id)
        pprint(fields)

    except requests.RequestException as e:
        print(f"HTTP request failed: {e}")
    except (KeyError, ValueError) as e:
        print(f"Data processing error: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")


if __name__ == "__main__":
    main()
