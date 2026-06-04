export interface UserData {
  email: string;
  loggedIn: boolean;
  isAdmin: boolean;
  limitations: {
    dailyJobs: number;
    filesize: string;
    todayJobsCount: number;
    diskSpace: string;
  }
}

interface Usage {
  current: number;
  max: number;
}

interface ResourceLimit {
  daily: Usage;
  monthly: Usage;
  extra: number;
}

export type RateLimits = Record<string, ResourceLimit>;
