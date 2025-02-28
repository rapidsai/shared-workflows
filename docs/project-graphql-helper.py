# Setting up imports and Globals
import requests
import json
import argparse
from pprint import pprint

# Building helper functions
def get_project_info(org, project_number, token):
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
    fields_response_json = json.loads(fields_response.text)

    not_project_fields = ['Title', 'Assignees', 'Labels', 'Linked pull requests', 'Reviewers', 'Repository', 'Milestone', 'Tracks']
    field_names = [{'name': field['name'], 'id': field['id']} for field in fields_response_json['data']['node']['fields']['nodes'] if field['name'] not in not_project_fields]
    fields = {field['name']:field for field in fields_response_json['data']['node']['fields']['nodes'] if field['name'] not in not_project_fields}

    for field in fields.values():
        if 'options' in field.keys():
            field['options'] = {option['name']: option['id'] for option in field['options']}

    return project_id, fields

def main():
    # Set up argument parser
    parser = argparse.ArgumentParser(description='GitHub Project GraphQL Helper')
    parser.add_argument('--token', '-t', required=True, help='GitHub personal access token')
    parser.add_argument('--org', '-o', required=True, help='GitHub organization name')
    parser.add_argument('--project', '-p', required=True, type=int, help='GitHub project number')
    
    args = parser.parse_args()
    
    # Use the provided arguments
    project_id, fields = get_project_info(org=args.org, project_number=args.project, token=args.token)
    
    pprint(project_id)
    pprint(fields)

if __name__ == "__main__":
    main()

