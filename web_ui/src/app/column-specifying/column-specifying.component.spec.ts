import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ColumnSpecifyingComponent } from './column-specifying.component';
import { MatDialogRef } from '@angular/material/dialog';
import { FileContent } from '../job-creation/jobs';
import { JobsService } from '../job-creation/jobs.service';
import { provideHttpClient } from '@angular/common/http';
import { Observable, of } from 'rxjs';

const mockContent = new FileContent(
  ',',
  ['CHROM', 'POSITION', 'REF', 'ALTERNATIVE'],
  [
    ['CHROM', 'POSITION', 'REF', 'ALTERNATIVE'],
    ['chr1', '123', 'A', 'GG'],
  ]
);

class MatDialogRefMock {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public close(value: boolean): void { }
}

class JobsServiceMock {
  public validateColumnSpecification(
  // eslint-disable-next-line @typescript-eslint/no-unused-vars, @stylistic/max-len
    fileHeader: string[], columnSpecification: Map<string, string>
  ): Observable<[string, string]> {
    return of(['', '']);
  }
}

describe('ColumnSpecifyingComponent', () => {
  let component: ColumnSpecifyingComponent;
  let fixture: ComponentFixture<ColumnSpecifyingComponent>;
  const mockMatDialogRef = new MatDialogRefMock();
  const jobServiceMock = new JobsServiceMock();

  beforeEach(async() => {
    await TestBed.configureTestingModule({
      imports: [ColumnSpecifyingComponent],
      providers: [
        { provide: MatDialogRef, useValue: mockMatDialogRef },
        { provide: JobsService, useValue: jobServiceMock },
        provideHttpClient(),
      ]
    }).compileComponents();

    fixture = TestBed.createComponent(ColumnSpecifyingComponent);
    component = fixture.componentInstance;
    component.fileContent = mockContent;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should check data', () => {
    const mappedFileContent: string[][] = [
      ['CHROM', 'POSITION', 'REF', 'ALTERNATIVE'],
      ['chr1', '123', 'A', 'GG'],
    ];
    expect(component.fileContent).toStrictEqual(new FileContent(
      ',',
      ['CHROM', 'POSITION', 'REF', 'ALTERNATIVE'],
      mappedFileContent
    ));
  });

  it('should map column to new name and emit the change to parent', () => {
    const columnsEmitterSpy = jest.spyOn(component.emitColumns, 'emit');

    component.onSelectName('pos', 'position');
    expect(component.mappedColumns).toStrictEqual(new Map([
      ['pos', 'position']
    ]));
    expect(columnsEmitterSpy).toHaveBeenCalledWith(new Map([['pos', 'position']]));
  });

  it('should map two columns to \'pos\' and emit to parent', () => {
    const columnsEmitterSpy = jest.spyOn(component.emitColumns, 'emit');
    component.mappedColumns = new Map([
      ['pos', 'alternative']
    ]);
    component.onSelectName('pos', 'position');
    expect(component.mappedColumns).toStrictEqual(new Map([
      ['pos', 'position']
    ]));
    expect(columnsEmitterSpy).toHaveBeenCalledWith(new Map([['pos', 'position']]));
  });

  it('should map \'chrom\' then \'alt\' to one column from file', () => {
    component.onSelectName('chrom', 'alternative');

    component.onSelectName('alt', 'alternative');
    expect(component.mappedColumns).toStrictEqual(new Map([
      ['alt', 'alternative']
    ]));
  });

  it('should remove selected column name for file column by selecting None option and emit to parent', () => {
    const columnsEmitterSpy = jest.spyOn(component.emitColumns, 'emit');
    component.mappedColumns = new Map([
      ['pos', 'alternative'],
      ['chrom', 'chr'],
    ]);

    component.onSelectName('None', 'alternative');
    expect(component.mappedColumns).toStrictEqual(new Map([
      ['chrom', 'chr'],
    ]));
    expect(columnsEmitterSpy).toHaveBeenCalledWith(new Map([['chrom', 'chr']]));
  });

  it('should set default names for file columns which are contained in list with names', () => {
    component.ngOnChanges();
    expect(component.mappedColumns).toStrictEqual(new Map([
      ['chrom', 'CHROM'],
      ['ref', 'REF'],
    ]));
  });

  it('should get the new name of a column from file', () => {
    component.mappedColumns = new Map([
      ['pos', 'POSITION_FILE'],
      ['chrom', 'CHROM_FILE'],
    ]);

    expect(component.getFileColumnNewName('POSITION_FILE')).toBe('pos');
  });

  it('should return null if new name for a column from file is not selected', () => {
    component.mappedColumns = new Map([
      ['pos', 'POSITION_FILE'],
      ['chrom', 'CHROM_FILE'],
    ]);

    expect(component.getFileColumnNewName('REF')).toBeNull();
  });
});
