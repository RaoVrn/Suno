import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'dart:convert';
import 'package:url_launcher/url_launcher.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:path_provider/path_provider.dart';
import 'package:permission_handler/permission_handler.dart';
import 'dart:async';

// Constants
const String DEFAULT_BACKEND_URL = 'http://127.0.0.1:8000';
const Duration REQUEST_TIMEOUT = Duration(seconds: 30);

// Helper function to get configuration
String getBackendUrl() {
  return DEFAULT_BACKEND_URL;  // Always use IP address for web
}

// Helper function for making HTTP requests
Future<http.Response> makeRequest(String url, {String method = 'GET', Map<String, String>? headers, Object? body}) async {
  try {
    final uri = Uri.parse(url);
    final requestHeaders = {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
      ...?headers,
    };

    late http.Response response;
    if (method == 'POST') {
      response = await http.post(
        uri,
        headers: requestHeaders,
        body: jsonEncode(body),
      ).timeout(REQUEST_TIMEOUT);
    } else {
      response = await http.get(uri, headers: requestHeaders).timeout(REQUEST_TIMEOUT);
    }
    return response;
  } on TimeoutException {
    throw Exception('Request timed out. Please try again.');
  } catch (e) {
    throw Exception('Failed to connect to server: $e');
  }
}

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  try {
    await dotenv.load();
  } catch (e) {
    // If .env file fails to load, use default values
    print('Failed to load .env file, using default values');
  }
  runApp(SunoApp());
}

class SunoApp extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Suno',
      theme: ThemeData(primarySwatch: Colors.deepPurple),
      home: ConvertScreen(),
    );
  }
}

class ConvertScreen extends StatefulWidget {
  @override
  _ConvertScreenState createState() => _ConvertScreenState();
}

class _ConvertScreenState extends State<ConvertScreen> {
  final TextEditingController _urlController = TextEditingController();
  String? downloadUrl;
  bool isLoading = false;
  bool isDownloading = false;
  Future<void> convert() async {
    final url = _urlController.text.trim();
    if (url.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Please enter a YouTube URL')),
      );
      return;
    }

    setState(() {
      isLoading = true;
      downloadUrl = null;  // Reset previous download URL
    });    try {
      final backendUrl = getBackendUrl();
      print('Converting video from URL: $url');
      
      final response = await makeRequest(
        '$backendUrl/convert',
        method: 'POST',
        body: {'youtube_url': url, 'quality': 'high'},
      );      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        final String downloadPath = data['download_url'] as String;
        setState(() {
          // Ensure the URL is properly formatted with the correct backend URL
          downloadUrl = Uri.parse(backendUrl).resolve(downloadPath).toString();
        });
      } else {
        final errorData = jsonDecode(response.body);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(errorData['detail'] ?? 'Conversion failed!')),
        );
      }    } catch (e) {
      print('Error occurred: $e'); // Debug log
      String errorMessage = 'Connection error: ';
      if (e.toString().contains('XMLHttpRequest error')) {
        errorMessage = 'Cannot connect to server. Please ensure:\n1. Backend is running on http://127.0.0.1:8000\n2. No firewall is blocking the connection';
      } else if (e.toString().contains('Connection refused')) {
        errorMessage = 'Server connection refused. Please ensure the backend server is running.';
      } else {
        errorMessage += e.toString();
      }
      
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(errorMessage),
          duration: Duration(seconds: 10),
          action: SnackBarAction(
            label: 'Dismiss',
            onPressed: () {
              ScaffoldMessenger.of(context).hideCurrentSnackBar();
            },
          ),
        ),
      );
      print('Full error details: $e');
    }

    setState(() => isLoading = false);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text('Suno - Convert YouTube to MP3')),
      body: Padding(
        padding: EdgeInsets.all(16),
        child: Column(
          children: [
            TextField(
              controller: _urlController,
              decoration: InputDecoration(
                labelText: 'YouTube URL',
                border: OutlineInputBorder(),
              ),
            ),
            SizedBox(height: 16),
            ElevatedButton(
              onPressed: isLoading ? null : convert,
              child: isLoading ? CircularProgressIndicator(color: Colors.white) : Text('Convert'),
            ),            SizedBox(height: 20),
            if (downloadUrl != null)
              Column(
                children: [
                  Text(
                    'Your MP3 is ready!',
                    style: TextStyle(
                      fontSize: 18,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  SizedBox(height: 10),                  ElevatedButton.icon(
                    icon: isDownloading ? SizedBox(
                      width: 24,
                      height: 24,
                      child: CircularProgressIndicator(
                        color: Colors.white,
                        strokeWidth: 2,
                      ),
                    ) : Icon(Icons.download),
                    label: Text(isDownloading ? 'Downloading...' : 'Download MP3'),
                    onPressed: isDownloading ? null : () async {
                      if (downloadUrl != null) {
                        setState(() => isDownloading = true);
                        try {
                          final url = Uri.parse(downloadUrl!);
                          if (await canLaunchUrl(url)) {
                            await launchUrl(url);
                          } else {
                            ScaffoldMessenger.of(context).showSnackBar(
                              SnackBar(content: Text('Could not open download link')),
                            );
                          }
                        } finally {
                          setState(() => isDownloading = false);
                        }
                      }
                    },
                  ),
                ],
              ),
          ],
        ),
      ),
    );
  }
}
