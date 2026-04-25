import { ComponentFixture, TestBed } from '@angular/core/testing';
import { JobCreationComponent } from './job-creation.component';
import { JobsService } from './jobs.service';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { BehaviorSubject, Observable, of } from 'rxjs';
import { Pipeline } from './pipelines';
import { FileContent } from './jobs';
import { SingleAnnotationService } from '../single-annotation.service';
import { UserData, UsersService } from '../users.service';

class SingleAnnotationServiceMock {
  public getGenomes(): Observable<string[]> {
    return of(['hg38', 'hg19']);
  }
}

const mockPipelines = [
  new Pipeline('id1', 'name1', 'content1', 'default'),
  new Pipeline('id2', 'name2', 'content2', 'default'),
  new Pipeline('id3', 'name3', 'content3', 'default'),
];
class JobsServiceMock {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public createVcfJob(file1: File, pipeline: string, content: string, genome: string): Observable<object> {
    return of({});
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars, @stylistic/max-len
  public createNonVcfJob(file1: File, pipeline: string, config: string, genome: string, fileSeparator: string): Observable<object> {
    return of({});
  }

  public getAnnotationPipelines(): Observable<Pipeline[]> {
    return of(mockPipelines);
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public createFilePreview(file: File): Observable<FileContent> {
    return of(new FileContent(',', ['chr', 'pos'], [['1', '123']]));
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public submitSeparator(file: File, separator: string): Observable<FileContent> {
    return of(new FileContent(',', ['chr', 'pos'], [['1', '123']]));
  }
}

class UserServiceMock {
  public userData = new BehaviorSubject<UserData>({
    email: 'email',
    loggedIn: true,
    isAdmin: false,
    limitations: {
      dailyJobs: 5,
      filesize: '64M',
      todayJobsCount: 4,
      variantCount: 1000,
      diskSpace: '1000'
    }
  });
}

describe('JobCreationComponent', () => {
  let component: JobCreationComponent;
  let fixture: ComponentFixture<JobCreationComponent>;
  let templateRef: HTMLElement;
  const jobsServiceMock = new JobsServiceMock();
  const singleAnnotationServiceMock = new SingleAnnotationServiceMock();
  const userServiceMock = new UserServiceMock();

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [JobCreationComponent],
      providers: [
        {
          provide: JobsService,
          useValue: jobsServiceMock
        },
        {
          provide: SingleAnnotationService,
          useValue: singleAnnotationServiceMock
        },
        {
          provide: UsersService,
          useValue: userServiceMock
        },
        provideHttpClient(),
        provideHttpClientTesting()
      ]
    }).compileComponents();

    fixture = TestBed.createComponent(JobCreationComponent);
    component = fixture.componentInstance;
    templateRef = fixture.debugElement.nativeElement as HTMLElement;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should remove uploaded file and emit to parent', () => {
    const emitFileSpy = jest.spyOn(component.emitFile, 'emit');
    const emitFileSeparatorSpy = jest.spyOn(component.emitFileSeparator, 'emit');
    const fileBlock = templateRef.querySelector('#uploaded-file-container');
    component.file = new File([], 'mockFile');

    component.resetState();

    expect(component.file).toBeNull();
    expect(fileBlock).toBeNull();
    expect(emitFileSpy).toHaveBeenCalledWith(null);
    expect(emitFileSeparatorSpy).toHaveBeenCalledWith('');
  });

  it('should upload file', () => {
    const mockFile = new File([], 'mockFile.vcf');
    const mockEvent = {
      target: { files: [mockFile] } as unknown as HTMLInputElement,
    } as unknown as Event;

    component.onUpload(mockEvent);
    expect(component.file).toBe(mockFile);
  });

  it('should show error message on file format validation and don\'t send file to parent', () => {
    const emitFileSpy = jest.spyOn(component.emitFile, 'emit');
    const mockFile = new File([], 'mockFile.pdf');
    const mockEvent = {
      target: { files: [mockFile] } as unknown as HTMLInputElement,
    } as unknown as Event;

    component.onUpload(mockEvent);
    expect(component.uploadError).toBe('Unsupported format!');
    expect(component.file).toStrictEqual(mockFile);
    expect(emitFileSpy).not.toHaveBeenCalledWith();
  });

  it('should upload csv file and sent query for preview', () => {
    const mockFile = new File([], 'mockFile.csv');
    const mockEvent = {
      target: { files: [mockFile] } as unknown as HTMLInputElement,
    } as unknown as Event;

    const createFilePreviewSpy = jest.spyOn(jobsServiceMock, 'createFilePreview');
    component.onUpload(mockEvent);
    expect(createFilePreviewSpy).toHaveBeenCalledWith(mockFile);
    expect(component.uploadError).toBe('');
  });

  ['vcf', 'vcf.gz', 'vcf.bgz'].forEach((extension) => {
    it(`should upload ${extension} file and emit it to parent`, () => {
      const emitFileSpy = jest.spyOn(component.emitFile, 'emit');
      const emitSepartorSpy = jest.spyOn(component.emitFileSeparator, 'emit');

      const mockFile = new File([], `mockFile.${extension}`);
      const mockEvent = {
        target: { files: [mockFile] } as unknown as HTMLInputElement,
      } as unknown as Event;

      component.onUpload(mockEvent);
      expect(component.uploadError).toBe('');
      expect(emitFileSpy).toHaveBeenCalledWith(mockFile);
      expect(emitSepartorSpy).not.toHaveBeenCalled();
    });
  });

  it('should drop csv file and emit it to parent', () => {
    const emitFileSpy = jest.spyOn(component.emitFile, 'emit');
    const emitSepartorSpy = jest.spyOn(component.emitFileSeparator, 'emit');

    const mockFile = new File([], 'mockFile.csv');
    const mockDataTransfer = { files: [mockFile] } as unknown as DataTransfer;
    const mockEvent = { dataTransfer: mockDataTransfer, preventDefault: jest.fn() } as unknown as DragEvent;

    component.onDropSuccess(mockEvent);

    expect(mockEvent.preventDefault).toHaveBeenCalledWith();
    expect(component.file).toBe(mockFile);
    expect(component.uploadError).toBe('');
    expect(emitFileSpy).toHaveBeenCalledWith(mockFile);
    expect(emitSepartorSpy).toHaveBeenCalledWith(',');
  });

  it('should drop file with unsupported format', () => {
    const emitFileSpy = jest.spyOn(component.emitFile, 'emit');
    const emitSepartorSpy = jest.spyOn(component.emitFileSeparator, 'emit');

    const mockFile = new File([], 'mockFile', { type: 'application/pdf' });
    const mockDataTransfer = { files: [mockFile] } as unknown as DataTransfer;
    const mockEvent = { dataTransfer: mockDataTransfer, preventDefault: jest.fn() } as unknown as DragEvent;

    component.onDropSuccess(mockEvent);

    expect(mockEvent.preventDefault).toHaveBeenCalledWith();
    expect(component.file).toStrictEqual(mockFile);
    expect(component.uploadError).toBe('Unsupported format!');
    expect(emitFileSpy).not.toHaveBeenCalledWith();
    expect(emitSepartorSpy).not.toHaveBeenCalled();
  });

  it('should do nothing if no files are dropped', () => {
    const mockDataTransfer = { files: [] } as unknown as DataTransfer;
    const mockEvent = { dataTransfer: mockDataTransfer, preventDefault: jest.fn() } as unknown as DragEvent;

    component.file = null;
    component.uploadError = '';
    component.onDropSuccess(mockEvent);

    expect(mockEvent.preventDefault).toHaveBeenCalledWith();
    expect(component.file).toBeNull();
    expect(component.uploadError).toBe('');
  });

  it('should submit new separator and emit it to parent', () => {
    const emitFileSeparatorSpy = jest.spyOn(component.emitFileSeparator, 'emit');
    const submitSeparatorSpy = jest.spyOn(jobsServiceMock, 'submitSeparator');

    component.file = new File([], 'mockFile');
    component.fileSeparator = '!';
    component.submitNewSeparator(',');

    expect(submitSeparatorSpy).toHaveBeenCalledWith(new File([], 'mockFile'), ',');
    expect(component.fileContent).toStrictEqual(new FileContent(',', ['chr', 'pos'], [['1', '123']]));
    expect(component.fileSeparator).toBe(',');
    expect(emitFileSeparatorSpy).toHaveBeenCalledWith(',');
  });

  it('should get new file headers and emit them to parent component', () => {
    const emitHeaderSpy = jest.spyOn(component.emitUpdatedFileHeader, 'emit');
    component.getColumns(new Map([
      ['pos', 'alternative']
    ]));
    expect(emitHeaderSpy).toHaveBeenCalledWith(new Map([
      ['pos', 'alternative']
    ]));
  });

  it('should set defualt genome and emit to parent', () => {
    const emitGenomeSpy = jest.spyOn(component.emitGenome, 'emit');
    component.ngOnInit();
    expect(component.selectedGenome).toBe('hg38');
    expect(emitGenomeSpy).toHaveBeenCalledWith('hg38');
  });

  it('should get genomes list on component init', () => {
    const getGenomesSpy = jest.spyOn(singleAnnotationServiceMock, 'getGenomes');
    component.ngOnInit();
    expect(getGenomesSpy).toHaveBeenCalledWith();
    expect(component.genomes).toStrictEqual(['hg38', 'hg19']);
  });
});
