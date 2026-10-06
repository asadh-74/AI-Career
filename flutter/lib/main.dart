import 'dart:convert';
import 'dart:typed_data';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:file_picker/file_picker.dart';
import 'package:http/http.dart' as http;
import 'package:url_launcher/url_launcher.dart';

const apiBase = String.fromEnvironment('API_BASE_URL', defaultValue: '');
const violet = Color(0xFF7450DC);
const recommendedBoards = <(String, String)>[
  ('Canonical', 'https://job-boards.greenhouse.io/canonical'),
  ('Smart Working Solutions', 'https://jobs.lever.co/smart-working-solutions'),
  ('Educative', 'https://jobs.lever.co/educative'),
  ('Xapo Bank', 'https://job-boards.greenhouse.io/xapo61'),
];
void main() => runApp(const CareerApp());

class Api {
  String? token;
  Uri uri(String path) => apiBase.isEmpty ? Uri.base.resolve('/api/$path') : Uri.parse('$apiBase/api/$path');
  Map<String, String> get headers => {'Content-Type': 'application/json', if (token != null) 'Authorization': 'Bearer $token'};
  Future<dynamic> request(String method, String path, [Object? body]) async {
    final u = uri(path);
    final r = switch (method) {
      'POST' => await http.post(u, headers: headers, body: jsonEncode(body ?? {})),
      'DELETE' => await http.delete(u, headers: headers),
      _ => await http.get(u, headers: headers),
    };
    dynamic data;
    try { data = jsonDecode(r.body); } catch (_) { data = {'detail': r.body}; }
    if (r.statusCode >= 400) throw Exception(data['detail'] ?? 'Request failed (${r.statusCode})');
    return data;
  }
  Future<void> login(String password) async { token = (await request('POST', 'auth/login', {'password': password}))['token']; }
  Future<Uint8List> getBytes(String path) async {
    final r=await http.get(uri(path),headers:{if(token!=null)'Authorization':'Bearer $token'});
    if(r.statusCode>=400)throw Exception('Download failed');
    return r.bodyBytes;
  }
  Future<void> upload(String kind, PlatformFile file) async {
    final req = http.MultipartRequest('POST', uri('documents'));
    req.headers['Authorization'] = 'Bearer $token';
    req.fields['kind'] = kind;
    req.files.add(http.MultipartFile.fromBytes('file', file.bytes!, filename: file.name));
    final response = await http.Response.fromStream(await req.send());
    if (response.statusCode >= 400) throw Exception(jsonDecode(response.body)['detail']);
  }
}
final api = Api();

class CareerApp extends StatelessWidget {
  const CareerApp({super.key});
  @override Widget build(BuildContext context) => MaterialApp(
    title: 'Career Atlas', debugShowCheckedModeBanner: false,
    theme: ThemeData(useMaterial3: true, colorScheme: ColorScheme.fromSeed(seedColor: violet),
      scaffoldBackgroundColor: const Color(0xFFF7F7FB), cardTheme: const CardThemeData(color: Colors.white, elevation: 0)),
    home: const Gate());
}
class Gate extends StatefulWidget { const Gate({super.key}); @override State<Gate> createState() => _GateState(); }
class _GateState extends State<Gate> {
  final controller = TextEditingController(); bool busy = false; String? error;
  @override void dispose(){controller.dispose();super.dispose();}
  Future<void> signIn() async { setState((){busy=true;error=null;}); try {await api.login(controller.text); if(mounted) Navigator.of(context).pushReplacement(MaterialPageRoute(builder: (_) => const Workspace()));} catch(e){setState(()=>error='$e');} finally {if(mounted)setState(()=>busy=false);} }
  @override Widget build(BuildContext context) => Scaffold(body: Center(child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 420),child: Padding(padding: const EdgeInsets.all(24),child: Column(mainAxisSize: MainAxisSize.min,crossAxisAlignment: CrossAxisAlignment.start,children:[
    const CircleAvatar(radius: 30, backgroundColor: violet,child: Text('✦',style: TextStyle(color: Colors.white,fontSize: 30))),const SizedBox(height:24),
    Text('Career Atlas',style: Theme.of(context).textTheme.headlineLarge?.copyWith(fontWeight: FontWeight.bold)),const SizedBox(height:8),
    const Text('Your private job search workspace. Sign in to see company roles and applications.'),const SizedBox(height:24),
    TextField(controller:controller, obscureText:true, onSubmitted:(_)=>signIn(),decoration:const InputDecoration(labelText:'Dashboard password',border:OutlineInputBorder())),
    if(error!=null) Padding(padding:const EdgeInsets.only(top:12),child:Text(error!,style:const TextStyle(color:Colors.red))),const SizedBox(height:15),
    FilledButton(onPressed:busy?null:signIn,child:Text(busy?'Signing in…':'Open workspace')),
  ])))));
}

class Workspace extends StatefulWidget { const Workspace({super.key}); @override State<Workspace> createState()=>_WorkspaceState(); }
class _WorkspaceState extends State<Workspace> {
  int tab=0; bool busy=false;
  List<dynamic> jobs=[],sources=[],documents=[],applications=[],jobStatuses=[],submittedApps=[],metrics=[],messages=[],followups=[];
  Map<String,dynamic> dashboard={};
  String query='', statusFilter='all', qualityMode='balanced';
  @override void initState(){super.initState();reload();}
  Future<void> reload() async { try {final result=await Future.wait([api.request('GET','jobs'),api.request('GET','sources'),api.request('GET','documents'),api.request('GET','applications'),api.request('GET','job-statuses'),api.request('GET','applications/submitted'),api.request('GET','v3/dashboard'),api.request('GET','v3/metrics'),api.request('GET','v3/messages'),api.request('GET','v3/followups')]);if(mounted)setState((){jobs=result[0];sources=result[1];documents=result[2];applications=result[3];jobStatuses=result[4];submittedApps=result[5];dashboard=Map<String,dynamic>.from(result[6] as Map);metrics=result[7];messages=result[8];followups=result[9];qualityMode=(dashboard['qualityMode']??'balanced').toString();});}catch(e){message('$e');} }
  void message(String text){if(mounted)ScaffoldMessenger.of(context).showSnackBar(SnackBar(content:Text(text)));}
  Future<void> scan() async {setState(()=>busy=true);try{final result=await api.request('POST','scan');await reload();final errors=(result['errors'] as List).cast<String>();message('Added ${result['added']} jobs.${errors.isEmpty?'':' Board errors: ${errors.join(', ')}'}');}catch(e){message('$e');}finally{if(mounted)setState(()=>busy=false);} }
  Future<void> open(String url) async {final uri=Uri.tryParse(url);if(uri!=null && ['https','http'].contains(uri.scheme))await launchUrl(uri,mode:LaunchMode.externalApplication);}
  Future<void> upload(String kind) async {try{final result=await FilePicker.platform.pickFiles(type:FileType.custom,allowedExtensions:['pdf'],withData:true);if(result==null)return;await api.upload(kind,result.files.single);await reload();message('$kind uploaded securely.');}catch(e){message('$e');}}
  Future<void> prepare(Map job,String kind) async {setState(()=>busy=true);try{final a=await api.request('POST','applications/prepare',{'job_id':job['id'],'document_kind':kind});await reload();if(mounted)showDialog(context:context,builder:(c)=>AlertDialog(title:Text('Application draft · ${job['company']}'),content:SingleChildScrollView(child:Text('Score ${a['score']}\n\n${a['rationale']}\n\n${a['draft']}')),actions:[TextButton(onPressed:()=>Navigator.pop(c),child:const Text('Close')),TextButton(onPressed:(){Navigator.pop(c);open(job['applyUrl']);},child:const Text('Open official form'))]));}catch(e){message('$e');}finally{if(mounted)setState(()=>busy=false);} }
  String statusFor(int jobId) => jobStatuses.where((s)=>s['jobId']==jobId).map((s)=>s['status'] as String).firstOrNull ?? 'not_submitted';
  Future<void> markStatus(Map job,String status) async {
    try { await api.request('POST','job-statuses',{'job_id':job['id'],'status':status});await reload();message(status=='submitted'?'Marked submitted: ${job['title']}':'Marked not submitted: ${job['title']}'); }
    catch(e){message('$e');}
  }
  Future<void> addSource() async {
    final company=TextEditingController(),website=TextEditingController();
    await showDialog(context:context,builder:(c)=>AlertDialog(title:const Text('Add company careers page'),content:Column(mainAxisSize:MainAxisSize.min,children:[
      TextField(controller:company,decoration:const InputDecoration(labelText:'Company name')),
      TextField(controller:website,decoration:const InputDecoration(labelText:'Greenhouse or Lever careers URL',hintText:'https://jobs.lever.co/company')),
    ]),actions:[TextButton(onPressed:()=>Navigator.pop(c),child:const Text('Cancel')),FilledButton(onPressed:()async{
      try{await api.request('POST','sources',{'company':company.text,'website':website.text.trim()});if(c.mounted)Navigator.pop(c);await reload();}
      catch(e){message('$e');}
    },child:const Text('Add board'))]));company.dispose();website.dispose();
  }
  Future<void> addRecommended(String company, String website) async {
    try {
      await api.request('POST', 'sources', {'company': company, 'website': website});
      await reload();
      message('$company added. Tap Find jobs to scan its open roles.');
    } catch (e) { message('$e'); }
  }
  Future<void> confirmApplication(Map application) async {
    final receipt=TextEditingController();
    await showDialog(context:context,builder:(c)=>AlertDialog(title:const Text('Record submitted application'),content:Column(mainAxisSize:MainAxisSize.min,children:[
      const Text('Only confirm after the employer website shows a successful submission. Enter its confirmation text, number or email subject.'),
      TextField(controller:receipt,decoration:const InputDecoration(labelText:'Employer confirmation')),
    ]),actions:[TextButton(onPressed:()=>Navigator.pop(c),child:const Text('Cancel')),FilledButton(onPressed:()async{
      if(receipt.text.trim().isEmpty)return;
      try{await api.request('POST','applications/${application['id']}/confirm',{'receipt':receipt.text.trim()});if(c.mounted)Navigator.pop(c);await reload();}
      catch(e){message('$e');}
    },child:const Text('Mark applied'))]));receipt.dispose();
  }
  @override Widget build(BuildContext context){final wide=MediaQuery.sizeOf(context).width>720;final filtered=jobs.where((x){final matchesText='${x['title']} ${x['company']} ${x['location']}'.toLowerCase().contains(query.trim().toLowerCase());final matchesStatus=statusFilter=='all'||statusFor(x['id'] as int)==statusFilter;return matchesText&&matchesStatus;}).toList();return Scaffold(appBar:AppBar(title:const Text('✦ Career Atlas',style:TextStyle(fontWeight:FontWeight.bold)),actions:[IconButton(tooltip:'Refresh',onPressed:reload,icon:const Icon(Icons.refresh)),IconButton(tooltip:'Sign out',onPressed:(){api.token=null;Navigator.of(context).pushReplacement(MaterialPageRoute(builder:(_)=>const Gate()));},icon:const Icon(Icons.logout))]),bottomNavigationBar:wide?null:NavigationBar(selectedIndex:tab,onDestinationSelected:(x)=>setState(()=>tab=x),destinations:const [NavigationDestination(icon:Icon(Icons.work_outline),label:'Jobs'),NavigationDestination(icon:Icon(Icons.approval_outlined),label:'Applications'),NavigationDestination(icon:Icon(Icons.check_circle_outline),label:'Submitted'),NavigationDestination(icon:Icon(Icons.settings_outlined),label:'Setup')]),body:Row(children:[if(wide)NavigationRail(selectedIndex:tab,onDestinationSelected:(x)=>setState(()=>tab=x),labelType:NavigationRailLabelType.all,destinations:const [NavigationRailDestination(icon:Icon(Icons.work_outline),label:Text('Jobs')),NavigationRailDestination(icon:Icon(Icons.approval_outlined),label:Text('Applications')),NavigationRailDestination(icon:Icon(Icons.check_circle_outline),label:Text('Submitted')),NavigationRailDestination(icon:Icon(Icons.settings_outlined),label:Text('Setup'))]),Expanded(child:Center(child:ConstrainedBox(constraints:const BoxConstraints(maxWidth:1100),child:Padding(padding:const EdgeInsets.all(16),child:switch(tab){0=>jobsView(filtered),1=>applicationsView(),2=>submittedView(),_=>setupView()}))))]));}
  Widget jobsView(List<dynamic> filtered)=>Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
    Row(children:[Expanded(child:Text('${jobs.length} company jobs',style:Theme.of(context).textTheme.headlineSmall)),FilledButton.icon(onPressed:busy?null:scan,icon:const Icon(Icons.auto_awesome),label:Text(busy?'Scanning…':'Find jobs'))]),
    const SizedBox(height:12),TextField(onChanged:(x)=>setState(()=>query=x),decoration:const InputDecoration(prefixIcon:Icon(Icons.search),hintText:'Search roles, companies or locations',filled:true,border:OutlineInputBorder())),
    const SizedBox(height:8),Wrap(spacing:8,children:[
      for(final option in [('all','All'),('not_submitted','To apply'),('submitted','Submitted')])
        ChoiceChip(label:Text(option.$2),selected:statusFilter==option.$1,onSelected:(_)=>setState(()=>statusFilter=option.$1)),
    ]),Text('Showing ${filtered.length} of ${jobs.length} roles'),
    const SizedBox(height:10),const Text('Explore other job sites (opens their live website)'),const SizedBox(height:5),
    Wrap(spacing:8,runSpacing:4,children:[
      OutlinedButton(onPressed:()=>open('https://pk.indeed.com/jobs?q=${Uri.encodeQueryComponent(query.trim().isEmpty ? 'software engineer' : query.trim())}&l=Pakistan'),child:const Text('Indeed ↗')),
      OutlinedButton(onPressed:()=>open('https://www.glassdoor.com/Job/pakistan-jobs-SRCH_IL.0,8_IN192.htm'),child:const Text('Glassdoor ↗')),
      OutlinedButton(onPressed:()=>open('https://www.linkedin.com/jobs/search/?keywords=${Uri.encodeQueryComponent(query.trim().isEmpty ? 'software engineer' : query.trim())}&f_WT=2'),child:const Text('LinkedIn ↗')),
      OutlinedButton(onPressed:()=>open('https://wellfound.com/jobs'),child:const Text('Wellfound ↗')),
      OutlinedButton(onPressed:()=>open('https://remoteok.com/'),child:const Text('Remote OK ↗')),
      OutlinedButton(onPressed:()=>open('https://remotive.com/remote-jobs'),child:const Text('Remotive ↗')),
      OutlinedButton(onPressed:()=>open('https://jobicy.com/'),child:const Text('Jobicy ↗')),
      OutlinedButton(onPressed:()=>open('https://himalayas.app/jobs'),child:const Text('Himalayas ↗')),
      OutlinedButton(onPressed:()=>open('https://weworkremotely.com/'),child:const Text('We Work Remotely ↗')),
      OutlinedButton(onPressed:()=>open('https://www.workingnomads.com/remote-jobs'),child:const Text('Working Nomads ↗')),
      OutlinedButton(onPressed:()=>open('https://nodesk.co/remote-jobs/'),child:const Text('NoDesk ↗')),
      OutlinedButton(onPressed:()=>open('https://remote.co/remote-jobs/'),child:const Text('Remote.co ↗')),
      OutlinedButton(onPressed:()=>open('https://www.arbeitnow.com/'),child:const Text('Arbeitnow ↗')),
      OutlinedButton(onPressed:()=>open('https://www.rozee.pk/EN/search/software-engineer-jobs-in-pakistan'),child:const Text('ROZEE.PK ↗')),
      OutlinedButton(onPressed:()=>open('https://www.mustakbil.com/'),child:const Text('Mustakbil ↗')),
    ]),const Text('Remote OK, Remotive, Jobicy, Himalayas, We Work Remotely and Arbeitnow are also imported automatically. Other sites open as live search/apply sources.'),const SizedBox(height:10),
    Expanded(child:filtered.isEmpty?Center(child:Text(jobs.isEmpty?'Add a company board in Setup, then tap Find jobs.':'No roles match these filters.')):ListView.builder(itemCount:filtered.length,itemBuilder:(c,i){
      final j=filtered[i] as Map;final submitted=statusFor(j['id'] as int)=='submitted';
      return Card(child:Padding(padding:const EdgeInsets.all(15),child:Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
        Text(j['title']??'',style:Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight:FontWeight.bold)),
        Text('${j['company']} · ${j['location']} · ${j['provider']}'),
        Text(submitted?'Submitted':'Not submitted',style:TextStyle(color:submitted?Colors.green.shade700:null,fontWeight:FontWeight.w600)),
        const SizedBox(height:10),Wrap(spacing:8,runSpacing:8,children:[
          OutlinedButton(onPressed:()=>open(j['applyUrl']),child:const Text('Official form ↗')),
          OutlinedButton(onPressed:busy?null:documents.isEmpty?()=>upload('Resume'):()=>prepare(j,documents.any((d)=>d['kind']=='Resume')?'Resume':'CV'),child:Text(documents.isEmpty?'Upload resume for AI match':'Match & draft')),
          OutlinedButton(onPressed:()=>markStatus(j,submitted?'not_submitted':'submitted'),child:Text(submitted?'Mark not submitted':'Mark submitted')),
        ])
      ])));
    }))
  ]);
  Widget applicationsView()=>Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
    Text('Applications',style:Theme.of(context).textTheme.headlineSmall),const SizedBox(height:8),
    Text('${jobStatuses.where((s)=>s['status']=='submitted').length} submitted · ${jobs.length-jobStatuses.where((s)=>s['status']=='submitted' && jobs.any((j)=>j['id']==s['jobId'])).length} not submitted'),
    const Text('Use Mark submitted on a job after you submit its official form. You can record earlier applications too.'),const SizedBox(height:12),
    Expanded(child:ListView(children:[
      ...jobStatuses.where((s)=>s['status']=='submitted').map((status){
        final related=jobs.where((j)=>j['id']==status['jobId']).toList();
        final j=related.isEmpty?null:related.first;
        return Card(child:ListTile(title:Text(j?['title']??'Job #${status['jobId']}'),subtitle:Text('${j?['company']??''} · Submitted'),trailing:j==null?null:TextButton(onPressed:()=>markStatus(j,'not_submitted'),child:const Text('Undo'))));
      }),
      if(applications.isNotEmpty)Padding(padding:const EdgeInsets.symmetric(vertical:10),child:Text('AI drafts',style:Theme.of(context).textTheme.titleMedium)),
      ...applications.map((item){
        final a=item as Map;final related=jobs.where((j)=>j['id']==a['jobId']).toList();final job=related.isEmpty?null:related.first;
        return Card(child:Padding(padding:const EdgeInsets.all(12),child:Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
          Text(job?['title']??'Job #${a['jobId']}',style:Theme.of(context).textTheme.titleMedium),
          Text('Score ${a['score']} · ${statusFor(a['jobId'] as int)=='submitted'?'Submitted':'Not submitted'}'),
          const SizedBox(height:6),Text(a['rationale']??''),
          ExpansionTile(title:const Text('Read application draft'),children:[Padding(padding:const EdgeInsets.all(12),child:SelectableText(a['draft']??''))]),
          OutlinedButton(onPressed:job==null?null:()=>open(job['applyUrl']),child:const Text('Official form ↗')),
        ])));
      }),
    ]))
  ]);
  Widget submittedView()=>Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
    Row(children:[
      Expanded(child:Text('Submitted Applications',style:Theme.of(context).textTheme.headlineSmall)),
      Chip(label:Text('${submittedApps.length} confirmed'))
    ]),
    const SizedBox(height:6),
    const Text('Only applications with a confirmed submission status and receipt appear here.'),
    const SizedBox(height:12),
    Expanded(child:submittedApps.isEmpty
      ? const Center(child:Text('No confirmed submissions yet.'))
      : ListView.builder(itemCount:submittedApps.length,itemBuilder:(c,i){
          final a=submittedApps[i] as Map;
          return Card(child:Padding(padding:const EdgeInsets.all(14),child:Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
            Row(crossAxisAlignment:CrossAxisAlignment.start,children:[
              const Icon(Icons.check_circle,color:Colors.green),
              const SizedBox(width:8),
              Expanded(child:Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
                Text(a['title']??'',style:Theme.of(context).textTheme.titleMedium?.copyWith(fontWeight:FontWeight.bold)),
                Text('${a['company']} · ${a['location']} · ${a['provider']}'),
              ]))
            ]),
            const SizedBox(height:8),
            Text('Match score: ${a['score']}'),
            Text('Submitted: ${a['submittedAt']}'),
            if((a['receipt']??'').toString().isNotEmpty) Text('Receipt: ${a['receipt']}'),
            const SizedBox(height:8),
            OutlinedButton(onPressed:()=>open(a['applyUrl']),child:const Text('View job ↗')),
          ])));
        }))
  ]);
  Widget setupView()=>ListView(children:[Text('Your setup',style:Theme.of(context).textTheme.headlineSmall),const SizedBox(height:14),const Text('Documents (private PDFs; selectable text required)'),...['CV','Resume'].map((kind)=>Card(child:ListTile(title:Text(kind),subtitle:Text(documents.where((d)=>d['kind']==kind).map((d)=>d['filename']).firstOrNull??'Not uploaded'),trailing:OutlinedButton(onPressed:()=>upload(kind),child:const Text('Upload'))))),const SizedBox(height:22),Row(children:[const Expanded(child:Text('Company career boards')),FilledButton(onPressed:addSource,child:const Text('Add company'))]),...sources.map((source)=>Card(child:ListTile(title:Text(source['company']),subtitle:Text('${source['provider']} · ${source['slug']}'),trailing:IconButton(icon:const Icon(Icons.delete_outline),onPressed:()async{await api.request('DELETE','sources/${source['id']}');await reload();})))),const SizedBox(height:18),Text('Suggested company boards',style:Theme.of(context).textTheme.titleMedium),const SizedBox(height:6),const Text('Add the boards you want to follow. Individual job locations and eligibility vary.'),...recommendedBoards.map((board)=>Card(child:ListTile(title:Text(board.$1),subtitle:Text(board.$2,overflow:TextOverflow.ellipsis),trailing:OutlinedButton(onPressed:sources.any((s)=>s['slug']==Uri.parse(board.$2).pathSegments.first)?null:()=>addRecommended(board.$1,board.$2),child:Text(sources.any((s)=>s['slug']==Uri.parse(board.$2).pathSegments.first)?'Added':'Add'))))),const SizedBox(height:20),const Text('Paste a Greenhouse or Lever careers URL to add another board. On this Free Render deployment, boards are scanned when you tap Find jobs. Employer forms require your review and confirmation.')]);
}
