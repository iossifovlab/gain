import { PipelineStatus } from '../socket-notifications/socket-notifications';

export class Pipeline {
  public constructor(
    public id: string,
    public name: string,
    public content: string,
    public type: 'user' | 'default',
    public status: PipelineStatus
  ) {}

  public static fromJsonArray(jsonArray: object[]): Pipeline[] {
    if (!jsonArray) {
      return undefined;
    }
    return jsonArray.map((json) => Pipeline.fromJson(json));
  }

  public static fromJson(json: object): Pipeline {
    if (!json) {
      return undefined;
    }

    return new Pipeline(
      (json['id'] as number).toString(),
      json['name'] as string,
      json['content'] as string,
      json['type'] as 'user' | 'default',
      json['status'] as PipelineStatus,
    );
  }
}