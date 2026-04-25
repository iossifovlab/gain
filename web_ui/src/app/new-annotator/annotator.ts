export class AnnotatorConfig {
  public constructor(
    public annotatorType: string,
    public annotatorUrl: string,
    public resources: AnnotatorConfigResource[]
  ) { }

  public static fromJson(json: object): AnnotatorConfig {
    if (!json) {
      return undefined;
    }

    const resources: AnnotatorConfigResource[] = [];
    Object.keys(json).forEach(key => {
      if (!['annotator_type', 'documentation_url'].includes(key)) {
        resources.push(new AnnotatorConfigResource(
          key,
          /* eslint-disable @typescript-eslint/no-unsafe-argument, @typescript-eslint/no-unsafe-member-access */
          json[key]['field_type'] || '',
          json[key]['resource_type'] || '',
          json[key]['value'] || '',
          null,
          json[key]['optional'],
          json[key]['attribute_type'] || ''
          /* eslint-enable */
        ));
      }
    });


    return new AnnotatorConfig(
      json['annotator_type'] as string,
      json['documentation_url'] as string,
      resources,
    );
  }
}

export class AnnotatorConfigResource {
  public constructor(
    public key: string,
    public fieldType: 'resource' | 'string' | 'bool' | 'attribute',
    public resourceType: string,
    public defaultValue: string | boolean,
    public possibleValues: string[],
    public optional: boolean,
    public attributeType: string,
  ) { }
}

export class ResourcePage {
  public constructor(
    public page: number,
    public totalPages: number,
    public resources: Resource[],
    public totalResources: number,
  ) {}

  public static fromJson(json: object): ResourcePage {
    if (!json) {
      return undefined;
    }

    return new ResourcePage(
      json['page'] as number,
      json['pages'] as number,
      Resource.fromJsonArray(json['resources'] as object[]),
      json['total_resources'] as number
    );
  }
}

export class Resource {
  public constructor(
    public fullId: string,
    public id: string,
    public type: string,
    public version: number,
    public url: string,
    public summary: string,
  ) {}

  public static fromJsonArray(jsonArray: object[]): Resource[] {
    if (!jsonArray) {
      return undefined;
    }
    return jsonArray.map((json) => Resource.fromJson(json));
  }

  public static fromJson(json: object): Resource {
    if (!json) {
      return undefined;
    }

    return new Resource(
      json['full_id'] as string,
      json['resource_id'] as string,
      json['type'] as string,
      json['version'] as number,
      json['url'] as string + '/index.html',
      json['summary'] as string,
    );
  }
}

export class AttributeData {
  public constructor(
    public name: string,
    public type: string,
    public source: string,
    public internal: boolean,
    public selectedByDefault: boolean,
    public description: string,
  ) {}

  public static fromJsonArray(jsonArray: object[]): AttributeData[] {
    if (!jsonArray) {
      return undefined;
    }
    return jsonArray.map((json) => AttributeData.fromJson(json));
  }

  public static fromJson(json: object): AttributeData {
    if (!json) {
      return undefined;
    }

    return new AttributeData(
      json['name'] as string,
      json['type'] as string,
      json['source'] as string,
      json['internal'] as boolean,
      json['default'] as boolean,
      json['description'] as string,
    );
  }
}

export class AttributePage {
  public constructor(
    public attributes: AttributeData[],
    public page: number,
    public totalPages: number,
    public totalAttributes: number,
  ) { }

  public static fromJson(json: object): AttributePage {
    if (!json) {
      return undefined;
    }

    return new AttributePage(
      AttributeData.fromJsonArray(json['attributes'] as object[]),
      json['page'] as number,
      json['total_pages'] as number,
      json['total_attributes'] as number,
    );
  }
}

export class ResourceAnnotatorConfigs {
  public constructor(
    public defaultAnnotator: string,
    public annotators: ResourceAnnotator[],
  ) { }

  public static fromJson(json: object): ResourceAnnotatorConfigs {
    if (!json) {
      return undefined;
    }

    const annotators: ResourceAnnotator[] = [];

    type Config = [string, object];

    for (const [, value] of Object.entries(json['configs'] as Config[])) {
      annotators.push(ResourceAnnotator.fromJson(value));
    }

    return new ResourceAnnotatorConfigs(
      json['default'] as string,
      annotators
    );
  }
}

export class ResourceAnnotator {
  public constructor(
    public annotatorType: string,
    public resourceJson: string,
  ) { }

  public static fromJsonArray(jsonArray: object[]): ResourceAnnotator[] {
    if (!jsonArray) {
      return undefined;
    }
    return jsonArray.map((json) => ResourceAnnotator.fromJson(json));
  }

  public static fromJson(json: object): ResourceAnnotator {
    if (!json) {
      return undefined;
    }

    const jsonString = JSON.stringify(json, (key: string, value: string) =>
      key === 'annotator_type' ? undefined : value
    );

    return new ResourceAnnotator(
      json['annotator_type'] as string,
      jsonString
    );
  }
}